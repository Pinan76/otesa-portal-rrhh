# -*- coding: utf-8 -*-
# ============================================================
# OTESA - Portal Web de RRHH
# Versión: 4.0 — Conectado a Supabase
#
# Funciones principales:
#   1. Login con contraseña de admin
#   2. Carga masiva de PDFs de nómina
#   3. Panel de estado de firmas
#   4. Envío de alertas a empleados pendientes
#   5. Bitácora de envíos
#   6. Alta de nuevo personal
#   7. Resultados de encuestas (conteo + detalle por persona y depto)
#   8. Mis Documentos (documentos personales subidos por empleados)
# ============================================================

import streamlit as st
import pandas as pd
import re
import io
from datetime import datetime
from pypdf import PdfReader
from supabase import create_client, Client

# ==========================================
# CONFIGURACIÓN
# ==========================================
SUPABASE_URL       = "https://msiulyfrohijawawwmrf.supabase.co"
SUPABASE_KEY       = st.secrets["SUPABASE_SERVICE_KEY"]
PASSWORD_ADMIN     = st.secrets["PASSWORD_ADMIN"]
RFC_EMPRESA        = "OTE2107019N1"
BUCKET_RECIBOS     = "recibos-nomina"
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
# FUNCIONES
# ==========================================
def extraer_datos_pdf(pdf_bytes: bytes) -> dict:
    try:
        reader = PdfReader(io.BytesIO(pdf_bytes))
        text   = reader.pages[0].extract_text()
        lines  = text.split('\n')

        todos_rfcs = re.findall(r'[A-Z&Ñ]{3,4}\s*\d{6}\s*[A-Z0-9]{3}', text)
        rfc_final  = "DESCONOCIDO"
        for rfc in todos_rfcs:
            clean = rfc.replace(" ", "").strip()
            if clean != RFC_EMPRESA:
                rfc_final = clean
                break

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

        semana  = 0
        año     = datetime.now().year
        periodo = ""

        match_periodo = re.search(r'Periodo:\s*(\d+)\s+\d+\s+Semanal\s+(\d{2}/\w+/\d{4})', text)
        if match_periodo:
            semana  = int(match_periodo.group(1))
            fecha_s = match_periodo.group(2)
            año     = int(fecha_s.split("/")[-1])
            periodo = f"Semana_{semana}"

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
    try:
        path = f"{CARPETA_ORIGINALES}/{nombre_archivo}"
        supabase.storage.from_(BUCKET_RECIBOS).upload(
            path,
            pdf_bytes,
            file_options={"content-type": "application/pdf", "upsert": "true"}
        )
        return supabase.storage.from_(BUCKET_RECIBOS).get_public_url(path)
    except Exception as e:
        st.error(f"Error subiendo {nombre_archivo}: {e}")
        return None


def crear_recibo_supabase(empresa_id: str, usuario_id: str, datos: dict, pdf_url: str, nombre_empleado: str) -> bool:
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
    import urllib.request
    import json

    resend_key = st.secrets.get("RESEND_KEY", "")
    if not resend_key:
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


def es_imagen(url: str) -> bool:
    return any(url.lower().endswith(ext) for ext in [".jpg", ".jpeg", ".png", ".webp", ".gif"])


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

tab1, tab2, tab3, tab4, tab5, tab6, tab7 = st.tabs([
    "📂 Carga Masiva",
    "🚨 Estado de Firmas",
    "📧 Alertas",
    "📊 Bitácora",
    "👤 Alta de Personal",
    "🗳️ Encuestas",
    "📁 Mis Documentos",
])

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
            progress   = st.progress(0)
            status     = st.empty()

            for i, archivo in enumerate(uploaded_files):
                status.text(f"Procesando {archivo.name}...")
                pdf_bytes = archivo.read()

                datos = extraer_datos_pdf(pdf_bytes)
                if "error" in datos:
                    resultados.append({
                        "Archivo":  archivo.name,
                        "Estado":   "❌ Error al leer PDF",
                        "RFC":      "-",
                        "Empleado": "-",
                    })
                    continue

                usuario = buscar_usuario_por_rfc(datos["rfc"], empresa_id)
                if not usuario:
                    resultados.append({
                        "Archivo":  archivo.name,
                        "Estado":   f"⚠️ RFC no encontrado: {datos['rfc']}",
                        "RFC":      datos["rfc"],
                        "Empleado": datos["nombre"],
                    })
                    continue

                pdf_url = subir_pdf_storage(pdf_bytes, archivo.name)
                if not pdf_url:
                    resultados.append({
                        "Archivo":  archivo.name,
                        "Estado":   "❌ Error al subir a Storage",
                        "RFC":      datos["rfc"],
                        "Empleado": usuario["nombre_completo"],
                    })
                    continue

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
        total      = len(df_status)
        firmados   = len(df_status[df_status["estado"] == "✅ FIRMADO"])
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
        pendientes_df = df_status[df_status["estado"] == "❌ PENDIENTE"]

        if not pendientes_df.empty:
            st.warning(f"⚠️ {len(pendientes_df)} empleado(s) con recibos pendientes")
            st.dataframe(pendientes_df[["nombre_empleado", "rfc", "periodo"]].rename(columns={
                "nombre_empleado": "Empleado",
                "rfc":             "RFC",
                "periodo":         "Período",
            }), use_container_width=True)

            st.divider()

            if st.button("📧 Enviar Alerta a Todos los Pendientes", type="primary"):
                enviados = 0
                errores  = 0

                for _, row in pendientes_df.iterrows():
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


# ==========================================
# TAB 5 — ALTA DE PERSONAL
# ==========================================
with tab5:
    st.subheader("Alta de nuevo personal")
    st.caption("Registra nuevos empleados en el sistema. Podrán registrarse en la app usando su RFC.")

    with st.form("form_alta_personal"):
        col1, col2 = st.columns(2)

        with col1:
            nombre_completo = st.text_input("Nombre completo *", placeholder="JUAN PEREZ GARCIA")
            rfc_nuevo       = st.text_input("RFC *", placeholder="PEGJ800101ABC")
            email_nuevo     = st.text_input("Correo electrónico *", placeholder="juan@ejemplo.com")
            area_nueva      = st.text_input("Área / Departamento *", placeholder="PRODUCCION")

        with col2:
            curp_nuevo    = st.text_input("CURP", placeholder="PEGJ800101HGRRCN09")
            nss_nuevo     = st.text_input("NSS (IMSS)", placeholder="12345678901")
            puesto_nuevo  = st.text_input("Puesto", placeholder="OPERADOR")
            fecha_ingreso = st.date_input("Fecha de ingreso", value=datetime.now())

        rol_nuevo = st.selectbox("Rol", ["empleado", "supervisor", "admin"])
        es_admin  = rol_nuevo == "admin"

        submitted = st.form_submit_button("👤 Registrar Empleado", type="primary")

        if submitted:
            if not nombre_completo or not rfc_nuevo or not email_nuevo or not area_nueva:
                st.error("⚠️ Los campos marcados con * son obligatorios.")
            else:
                try:
                    check = supabase.table("usuarios") \
                        .select("id") \
                        .eq("rfc_empleado", rfc_nuevo.upper().strip()) \
                        .eq("empresa_id", empresa_id) \
                        .execute()

                    if check.data:
                        st.error(f"⚠️ El RFC {rfc_nuevo.upper()} ya está registrado.")
                    else:
                        nuevo_usuario = {
                            "empresa_id":      empresa_id,
                            "nombre_completo": nombre_completo.upper().strip(),
                            "rfc_empleado":    rfc_nuevo.upper().strip(),
                            "email":           email_nuevo.lower().strip(),
                            "area":            area_nueva.upper().strip(),
                            "rol":             rol_nuevo,
                            "es_admin":        es_admin,
                            "estado":          "ACTIVO",
                        }
                        if curp_nuevo:
                            nuevo_usuario["curp"] = curp_nuevo.upper().strip()
                        if nss_nuevo:
                            nuevo_usuario["nss"] = nss_nuevo.strip()
                        if puesto_nuevo:
                            nuevo_usuario["puesto"] = puesto_nuevo.upper().strip()

                        resp_insert = supabase.table("usuarios").insert(nuevo_usuario).execute()

                        if resp_insert.data:
                            st.success(f"✅ Empleado **{nombre_completo.upper()}** registrado correctamente.")
                            st.info("📱 El empleado podrá registrarse en la app usando su RFC.")
                        else:
                            st.error("❌ Error al registrar el empleado.")
                except Exception as e:
                    st.error(f"Error: {e}")

    st.divider()
    st.subheader("Empleados registrados")

    try:
        resp_empleados = supabase.table("usuarios") \
            .select("nombre_completo, rfc_empleado, email, area, rol, estado, auth_user_id") \
            .eq("empresa_id", empresa_id) \
            .order("nombre_completo") \
            .execute()

        if resp_empleados.data:
            df_empleados = pd.DataFrame(resp_empleados.data)
            df_empleados["App"] = df_empleados["auth_user_id"].apply(
                lambda x: "✅ Registrado" if x else "⏳ Pendiente"
            )
            df_empleados = df_empleados.drop(columns=["auth_user_id"])
            st.dataframe(
                df_empleados.rename(columns={
                    "nombre_completo": "Nombre",
                    "rfc_empleado":    "RFC",
                    "email":           "Email",
                    "area":            "Área",
                    "rol":             "Rol",
                    "estado":          "Estado",
                }),
                use_container_width=True
            )
            st.caption(f"Total: {len(df_empleados)} empleados")
        else:
            st.info("No hay empleados registrados.")
    except Exception as e:
        st.error(f"Error cargando empleados: {e}")


# ==========================================
# TAB 6 — ENCUESTAS
# ==========================================
with tab6:
    st.subheader("Resultados de Encuestas")

    try:
        resp_encuestas = supabase.table("publicaciones") \
            .select("id, titulo, contenido, fecha_limite, activo") \
            .eq("empresa_id", empresa_id) \
            .eq("tipo", "ENCUESTA") \
            .order("created_at", desc=True) \
            .execute()

        if resp_encuestas.data:
            for encuesta in resp_encuestas.data:
                estado_txt = "🟢 Activa" if encuesta.get("activo") else "🔴 Cerrada"
                with st.expander(f"📊 {encuesta['titulo']} — {estado_txt}"):
                    st.caption(encuesta.get("contenido", ""))
                    fecha_lim = encuesta.get("fecha_limite", "")
                    if fecha_lim:
                        st.caption(f"Fecha límite: {fecha_lim[:10]}")

                    resp_votos = supabase.table("votos") \
                        .select("opcion_elegida, area, created_at, usuarios(nombre_completo, rfc_empleado)") \
                        .eq("publicacion_id", encuesta["id"]) \
                        .execute()

                    if resp_votos.data:
                        votos_lista = []
                        for v in resp_votos.data:
                            usuario_data = v.get("usuarios") or {}
                            votos_lista.append({
                                "Voto":       v.get("opcion_elegida", "-"),
                                "Empleado":   usuario_data.get("nombre_completo", "Desconocido"),
                                "RFC":        usuario_data.get("rfc_empleado", "-"),
                                "Área":       v.get("area", "Sin área"),
                                "Fecha Voto": v.get("created_at", "")[:10],
                            })

                        df_votos    = pd.DataFrame(votos_lista)
                        total_votos = len(df_votos)

                        conteo = df_votos["Voto"].value_counts().reset_index()
                        conteo.columns = ["Opción", "Votos"]
                        conteo["Porcentaje"] = (conteo["Votos"] / total_votos * 100).round(1).astype(str) + "%"

                        st.metric("Total de votos", total_votos)

                        col1, col2 = st.columns([2, 1])
                        with col1:
                            st.bar_chart(conteo.set_index("Opción")["Votos"])
                        with col2:
                            st.dataframe(conteo, use_container_width=True, hide_index=True)

                        st.divider()

                        st.markdown("**Votos por Departamento**")
                        df_depto = df_votos.groupby(["Área", "Voto"]).size().reset_index(name="Cantidad")
                        st.dataframe(df_depto, use_container_width=True, hide_index=True)

                        st.divider()

                        st.markdown("**Detalle por Persona**")
                        st.dataframe(
                            df_votos[["Empleado", "RFC", "Área", "Voto", "Fecha Voto"]],
                            use_container_width=True,
                            hide_index=True
                        )
                    else:
                        st.info("Sin votos registrados aún.")
        else:
            st.info("No hay encuestas registradas para esta empresa.")
    except Exception as e:
        st.error(f"Error cargando encuestas: {e}")


# ==========================================
# TAB 7 — MIS DOCUMENTOS
# ==========================================
with tab7:
    st.subheader("Documentos personales de empleados")
    st.caption("Documentos subidos por los empleados desde la app (PDFs e imágenes).")

    try:
        resp_docs = supabase.table("publicaciones") \
            .select("id, titulo, archivo_url, created_at, empleado_id") \
            .eq("empresa_id", empresa_id) \
            .eq("tipo", "DOCUMENTO_PERSONAL") \
            .order("created_at", desc=True) \
            .execute()

        if resp_docs.data:
            # Obtener datos de empleados en una sola consulta
            empleado_ids = list({doc["empleado_id"] for doc in resp_docs.data if doc.get("empleado_id")})
            resp_usuarios = supabase.table("usuarios") \
                .select("id, nombre_completo, rfc_empleado, area") \
                .in_("id", empleado_ids) \
                .execute()

            usuarios_map = {u["id"]: u for u in (resp_usuarios.data or [])}

            docs_lista = []
            for doc in resp_docs.data:
                usuario_data = usuarios_map.get(doc.get("empleado_id"), {})
                docs_lista.append({
                    "Empleado": usuario_data.get("nombre_completo", "Desconocido"),
                    "RFC":      usuario_data.get("rfc_empleado", "-"),
                    "Área":     usuario_data.get("area", "-"),
                    "Título":   doc.get("titulo", "Sin título"),
                    "Fecha":    doc.get("created_at", "")[:10],
                    "URL":      doc.get("archivo_url", ""),
                })

            df_docs = pd.DataFrame(docs_lista)

            m1, m2 = st.columns(2)
            m1.metric("Total documentos", len(df_docs))
            m2.metric("Empleados con documentos", df_docs["Empleado"].nunique())

            st.divider()

            empleados_lista = ["Todos"] + sorted(df_docs["Empleado"].unique().tolist())
            filtro_empleado = st.selectbox("Filtrar por empleado", empleados_lista)

            if filtro_empleado != "Todos":
                df_docs = df_docs[df_docs["Empleado"] == filtro_empleado]

            for _, row in df_docs.iterrows():
                with st.expander(f"📄 {row['Título']} — {row['Empleado']} ({row['Fecha']})"):
                    col1, col2 = st.columns([2, 1])
                    with col2:
                        st.write(f"**Empleado:** {row['Empleado']}")
                        st.write(f"**RFC:** {row['RFC']}")
                        st.write(f"**Área:** {row['Área']}")
                        st.write(f"**Fecha:** {row['Fecha']}")
                    with col1:
                        url = row["URL"]
                        if url:
                            if es_imagen(url):
                                st.image(url, use_container_width=True)
                            else:
                                st.markdown(f"[📥 Abrir documento]({url})", unsafe_allow_html=True)
                        else:
                            st.warning("Sin archivo adjunto")
        else:
            st.info("No hay documentos personales subidos por los empleados.")
    except Exception as e:
        st.error(f"Error cargando documentos: {e}")
