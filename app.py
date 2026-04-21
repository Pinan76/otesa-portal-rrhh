# -*- coding: utf-8 -*-
# ============================================================
# OTESA - Portal Web de RRHH
# Versión: 2.0 — Conectado a Supabase
#
# Funciones principales:
#   1. Login con contraseña de admin
#   2. Carga masiva de PDFs de nómina
#   3. Procesamiento automático — lee RFC y asigna a empleado
#   4. Panel de estado de firmas
#   5. Envío de alertas a empleados pendientes
#   6. Bitácora de envíos
# ============================================================

import streamlit as st
import pandas as pd
import re
import io
import os
from datetime import datetime
from pypdf import PdfReader
from supabase import create_client, Client

# ==========================================
# CONFIGURACIÓN
# ==========================================
SUPABASE_URL      = "https://msiulyfrohijawawwmrf.supabase.co"
SUPABASE_KEY = st.secrets["SUPABASE_SERVICE_KEY"]
PASSWORD_ADMIN    = st.secrets["PASSWORD_ADMIN"]
RFC_EMPRESA       = "OTE2107019N1"
BUCKET_RECIBOS    = "recibos-nomina"
CARPETA_ORIGINALES = "originales"

st.set_page_config(
    page_title="OTESA - Portal RRHH",
    page_icon="📋",
    layout="wide"
)

# ==========================================
# CLIENTE SUPABASE
# ==========================================
@st.cache_resource
def get_supabase() -> Client:
    return create_client(SUPABASE_URL, SUPABASE_KEY)

supabase = get_supabase()

# ==========================================
# SESSION STATE
# ==========================================
if "admin" not in st.session_state:
    st.session_state.admin = False

# ==========================================
# FUNCIONES DE PROCESAMIENTO DE PDF
# ==========================================
def extraer_datos_pdf(pdf_bytes: bytes) -> dict:
    """Extrae RFC, nombre y datos del recibo desde un PDF de CONTPAQi."""
    try:
        reader = PdfReader(io.BytesIO(pdf_bytes))
        text   = reader.pages[0].extract_text()
        lines  = text.split('\n')

        # --- RFC del empleado ---
        todos_rfcs = re.findall(r'[A-Z&Ñ]{3,4}\s*\d{6}\s*[A-Z0-9]{3}', text)
        rfc_final  = "DESCONOCIDO"
        for rfc in todos_rfcs:
            clean = rfc.replace(" ", "").strip()
            if clean != RFC_EMPRESA:
                rfc_final = clean
                break

        # --- Nombre del empleado ---
        nombre_final = "Colaborador"
        if rfc_final != "DESCONOCIDO":
            idx_rfc = -1
            for i, line in enumerate(lines):
                if rfc_final in line.replace(" ", ""):
                    idx_rfc = i
                    break
            if idx_rfc != -1:
                candidatos = [
                    lines[idx_rfc].replace(rfc_final, "").strip(),
                    lines[idx_rfc - 1].strip() if idx_rfc > 0 else "",
                    lines[idx_rfc + 1].strip() if idx_rfc < len(lines) - 1 else "",
                ]
                for cand in candidatos:
                    if len(cand) > 7 and any(c.isalpha() for c in cand):
                        if "RFC" not in cand and "CURP" not in cand:
                            nombre_final = cand
                            break

        # --- Semana y año ---
        semana = 0
        año    = datetime.now().year
        periodo = ""

        match_periodo = re.search(r'Periodo:\s*(\d+)\s+\d+\s+Semanal\s+(\d{2}/\w+/\d{4})', text)
        if match_periodo:
            semana  = int(match_periodo.group(1))
            fecha_s = match_periodo.group(2)
            año     = int(fecha_s.split("/")[-1])
            periodo = f"Semana_{semana}"

        # --- Monto neto ---
        monto = 0.0
        match_neto = re.search(r'Neto del recibo\s*\$\s*([\d,]+\.\d{2})', text)
        if match_neto:
            monto = float(match_neto.group(1).replace(",", ""))

        return {
            "rfc":     rfc_final,
            "nombre":  nombre_final,
            "semana":  semana,
            "año":     año,
            "periodo": periodo,
            "monto":   monto,
        }
    except Exception as e:
        return {"error": str(e)}


def buscar_usuario_por_rfc(rfc: str, empresa_id: str) -> dict | None:
    """Busca un usuario en Supabase por RFC y empresa."""
    try:
        resp = supabase.table("usuarios") \
            .select("id, nombre_completo, email, area") \
            .eq("rfc_empleado", rfc) \
            .eq("empresa_id", empresa_id) \
            .eq("estado", "ACTIVO") \
            .single() \
            .execute()
        return resp.data
    except:
        return None


def subir_pdf_storage(pdf_bytes: bytes, nombre_archivo: str) -> str | None:
    """Sube un PDF al bucket de Supabase Storage y regresa la URL pública."""
    try:
        path = f"{CARPETA_ORIGINALES}/{nombre_archivo}"
        supabase.storage.from_(BUCKET_RECIBOS).upload(
            path,
            pdf_bytes,
            {"content-type": "application/pdf", "upsert": "true"}
        )
        url = supabase.storage.from_(BUCKET_RECIBOS).get_public_url(path)
        return url
    except Exception as e:
        st.error(f"Error subiendo {nombre_archivo}: {e}")
        return None


def crear_recibo_supabase(empresa_id: str, usuario_id: str, datos: dict, pdf_url: str, nombre_empleado: str) -> bool:
    """Inserta un registro en la tabla recibos."""
    try:
        supabase.table("recibos").insert({
            "empresa_id":      empresa_id,
            "usuario_id":      usuario_id,
            "periodo":         datos["periodo"],
            "semana":          datos["semana"],
            "año":             datos["año"],
            "monto":           datos["monto"],
            "pdf_url":         pdf_url,
            "estado":          "PENDIENTE",
            "nombre_empleado": nombre_empleado,
            "rfc":             datos["rfc"],
        }).execute()
        return True
    except Exception as e:
        st.error(f"Error creando recibo: {e}")
        return False


def obtener_empresa(empresa_id: str) -> dict | None:
    """Obtiene datos de la empresa."""
    try:
        resp = supabase.table("empresas") \
            .select("*") \
            .eq("id", empresa_id) \
            .single() \
            .execute()
        return resp.data
    except:
        return None


def obtener_status_recibos(empresa_id: str) -> pd.DataFrame:
    """Obtiene el status de todos los recibos de la empresa."""
    try:
        resp = supabase.table("recibos") \
            .select("nombre_empleado, rfc, periodo, semana, monto, estado, fecha_firma") \
            .eq("empresa_id", empresa_id) \
            .order("semana", desc=True) \
            .execute()
        if resp.data:
            df = pd.DataFrame(resp.data)
            df["estado"] = df["estado"].apply(
                lambda x: "✅ FIRMADO" if x in ["FIRMADO", "ENVIADO"] else "❌ PENDIENTE"
            )
            return df
        return pd.DataFrame()
    except:
        return pd.DataFrame()


def enviar_alerta_resend(email_empleado: str, nombre_empleado: str, periodo: str) -> bool:
    """Envía alerta de recibo pendiente via Resend."""
    import urllib.request
    import json

    resend_key = st.secrets.get("RESEND_KEY", "")
    if not resend_key:
        st.error("No se encontró RESEND_KEY en secrets")
        return False

    payload = {
        "from":    "OTESA Nómina <nomina@trajesespanoles.mx>",
        "to":      [email_empleado],
        "subject": f"Recibo de Nómina Pendiente - {periodo}",
        "text":    f"Estimado(a) {nombre_empleado},\n\nTienes un recibo de nómina pendiente de firma correspondiente al período {periodo}.\n\nPor favor abre la app OTESA Nómina para firmarlo.\n\nAtentamente,\nRecursos Humanos\nOperadora de Trajes Españoles",
    }

    req = urllib.request.Request(
        "https://api.resend.com/emails",
        data=json.dumps(payload).encode(),
        headers={
            "Content-Type":  "application/json",
            "Authorization": f"Bearer {resend_key}",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req) as resp:
            return resp.status == 200
    except:
        return False


# ==========================================
# SIDEBAR — LOGIN
# ==========================================
with st.sidebar:
    st.image("https://msiulyfrohijawawwmrf.supabase.co/storage/v1/object/public/logos-empresas/otesa_logo.png", width=150)
    st.title("OTESA - RRHH")
    st.divider()

    if not st.session_state.admin:
        pwd = st.text_input("Contraseña de acceso", type="password")
        if st.button("Entrar", type="primary"):
            if pwd == PASSWORD_ADMIN:
                st.session_state.admin = True
                st.rerun()
            else:
                st.error("Contraseña incorrecta")
    else:
        st.success("✅ Sesión activa")
        # Selector de empresa (para futuro multiempresa)
        st.divider()
        empresa_id_input = st.text_input(
            "ID de empresa",
            value=st.secrets.get("EMPRESA_ID", ""),
            help="UUID de la empresa en Supabase"
        )
        st.session_state.empresa_id = empresa_id_input

        if st.button("🔒 Cerrar Sesión"):
            st.session_state.admin = False
            st.rerun()

# ==========================================
# PANEL PRINCIPAL
# ==========================================
if not st.session_state.admin:
    st.title("🔐 Portal de Recursos Humanos")
    st.info("Ingresa tu contraseña en el panel lateral para acceder.")
    st.stop()

empresa_id = st.session_state.get("empresa_id", "")
if not empresa_id:
    st.warning("⚠️ Ingresa el ID de empresa en el panel lateral.")
    st.stop()

empresa = obtener_empresa(empresa_id)
nombre_empresa = empresa["nombre_comercial"] if empresa else "Empresa"

st.title(f"📋 Panel RRHH — {nombre_empresa}")
st.divider()

tab1, tab2, tab3, tab4 = st.tabs(["📂 Carga Masiva", "🚨 Estado de Firmas", "📧 Alertas", "📊 Bitácora"])

# ==========================================
# TAB 1 — CARGA MASIVA
# ==========================================
with tab1:
    st.subheader("Carga masiva de recibos de nómina")
    st.caption("Sube los PDFs generados por CONTPAQi. El sistema asignará cada recibo al empleado correspondiente automáticamente.")

    uploaded_files = st.file_uploader(
        "Selecciona los PDFs de nómina",
        type=["pdf"],
        accept_multiple_files=True
    )

    if uploaded_files:
        st.info(f"📄 {len(uploaded_files)} archivo(s) seleccionado(s)")

        if st.button("🚀 Procesar y Subir Recibos", type="primary"):
            resultados = []
            progress = st.progress(0)
            status   = st.empty()

            for i, archivo in enumerate(uploaded_files):
                status.text(f"Procesando {archivo.name}...")
                pdf_bytes = archivo.read()

                # 1. Extraer datos del PDF
                datos = extraer_datos_pdf(pdf_bytes)
                if "error" in datos:
                    resultados.append({
                        "Archivo": archivo.name,
                        "Estado":  "❌ Error al leer PDF",
                        "RFC":     "-",
                        "Empleado": "-",
                    })
                    continue

                # 2. Buscar empleado en Supabase
                usuario = buscar_usuario_por_rfc(datos["rfc"], empresa_id)
                if not usuario:
                    resultados.append({
                        "Archivo":  archivo.name,
                        "Estado":   f"⚠️ RFC no encontrado: {datos['rfc']}",
                        "RFC":      datos["rfc"],
                        "Empleado": datos["nombre"],
                    })
                    continue

                # 3. Subir PDF a Storage
                pdf_url = subir_pdf_storage(pdf_bytes, archivo.name)
                if not pdf_url:
                    resultados.append({
                        "Archivo":  archivo.name,
                        "Estado":   "❌ Error al subir a Storage",
                        "RFC":      datos["rfc"],
                        "Empleado": usuario["nombre_completo"],
                    })
                    continue

                # 4. Crear registro en tabla recibos
                ok = crear_recibo_supabase(
                    empresa_id,
                    usuario["id"],
                    datos,
                    pdf_url,
                    usuario["nombre_completo"]
                )

                resultados.append({
                    "Archivo":  archivo.name,
                    "Estado":   "✅ Cargado correctamente" if ok else "❌ Error al crear recibo",
                    "RFC":      datos["rfc"],
                    "Empleado": usuario["nombre_completo"],
                    "Semana":   datos["semana"],
                    "Monto":    f"${datos['monto']:,.2f}",
                })

                progress.progress((i + 1) / len(uploaded_files))

            status.text("✅ Proceso completado")
            st.dataframe(pd.DataFrame(resultados), use_container_width=True)


# ==========================================
# TAB 2 — ESTADO DE FIRMAS
# ==========================================
with tab2:
    st.subheader("Estado de firmas de recibos")

    col1, col2 = st.columns([3, 1])
    with col2:
        if st.button("🔄 Actualizar"):
            st.rerun()

    df_status = obtener_status_recibos(empresa_id)

    if not df_status.empty:
        total     = len(df_status)
        firmados  = len(df_status[df_status["estado"] == "✅ FIRMADO"])
        pendientes = total - firmados

        m1, m2, m3 = st.columns(3)
        m1.metric("Total recibos", total)
        m2.metric("Firmados", firmados)
        m3.metric("Pendientes", pendientes)

        st.divider()

        filtro = st.selectbox("Filtrar por estado", ["Todos", "Pendientes", "Firmados"])
        if filtro == "Pendientes":
            df_status = df_status[df_status["estado"] == "❌ PENDIENTE"]
        elif filtro == "Firmados":
            df_status = df_status[df_status["estado"] == "✅ FIRMADO"]

        st.dataframe(
            df_status.rename(columns={
                "nombre_empleado": "Empleado",
                "rfc":             "RFC",
                "periodo":         "Período",
                "semana":          "Semana",
                "monto":           "Monto",
                "estado":          "Estado",
                "fecha_firma":     "Fecha Firma",
            }),
            use_container_width=True
        )
    else:
        st.info("No hay recibos registrados para esta empresa.")


# ==========================================
# TAB 3 — ALERTAS
# ==========================================
with tab3:
    st.subheader("Enviar alertas a empleados con recibos pendientes")

    df_status = obtener_status_recibos(empresa_id)

    if not df_status.empty:
        pendientes = df_status[df_status["estado"] == "❌ PENDIENTE"]

        if not pendientes.empty:
            st.warning(f"⚠️ {len(pendientes)} empleado(s) con recibos pendientes")
            st.dataframe(pendientes[["nombre_empleado", "rfc", "periodo"]].rename(columns={
                "nombre_empleado": "Empleado",
                "rfc":             "RFC",
                "periodo":         "Período",
            }), use_container_width=True)

            st.divider()

            if st.button("📧 Enviar Alerta a Todos los Pendientes", type="primary"):
                # Obtener emails de los empleados pendientes
                rfcs_pendientes = pendientes["rfc"].tolist()
                enviados = 0
                errores  = 0

                for _, row in pendientes.iterrows():
                    try:
                        resp = supabase.table("usuarios") \
                            .select("email, nombre_completo") \
                            .eq("rfc_empleado", row["rfc"]) \
                            .eq("empresa_id", empresa_id) \
                            .single() \
                            .execute()

                        if resp.data and resp.data.get("email"):
                            ok = enviar_alerta_resend(
                                resp.data["email"],
                                resp.data["nombre_completo"],
                                row["periodo"]
                            )
                            if ok:
                                enviados += 1
                            else:
                                errores += 1
                    except:
                        errores += 1

                st.success(f"✅ {enviados} alerta(s) enviada(s)")
                if errores:
                    st.error(f"❌ {errores} error(es) al enviar")
        else:
            st.success("✅ Todos los empleados han firmado sus recibos.")
    else:
        st.info("No hay recibos registrados.")


# ==========================================
# TAB 4 — BITÁCORA
# ==========================================
with tab4:
    st.subheader("Bitácora de envíos")

    try:
        resp = supabase.table("envios_log") \
            .select("created_at, destinatario, tipo, estado, detalle, usuarios(nombre_completo)") \
            .eq("empresa_id", empresa_id) \
            .order("created_at", desc=True) \
            .limit(100) \
            .execute()

        if resp.data:
            df_log = pd.DataFrame(resp.data)
            st.dataframe(df_log, use_container_width=True)
        else:
            st.info("No hay registros en la bitácora.")
    except Exception as e:
        st.error(f"Error cargando bitácora: {e}")
