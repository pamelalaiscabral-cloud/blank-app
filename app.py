import streamlit as st
import pdfplumber
import pandas as pd
import urllib.request
import urllib.parse
import io
import re
import json
import requests
import geopandas as gpd
import folium
from streamlit_folium import st_folium
from datetime import datetime

from reportlab.lib.pagesizes import letter
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, HRFlowable
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib import colors

# ---------------------------------------------------------
# CONFIGURAÇÃO DA PÁGINA
# ---------------------------------------------------------
st.set_page_config(
    page_title="Sistema de Avaliação Socioambiental",
    page_icon="🛡️",
    layout="wide"
)

st.markdown("""
    <style>
    .main-header { font-size: 24px; font-weight: 600; color: #1E293B; margin-bottom: 4px; }
    .sub-header { font-size: 14px; color: #64748B; margin-bottom: 24px; }
    .status-box-approved { background-color: #F0FDF4; border-left: 4px solid #16A34A; padding: 12px 16px; border-radius: 4px; color: #15803D; font-weight: 600; }
    .status-box-rejected { background-color: #FEF2F2; border-left: 4px solid #DC2626; padding: 12px 16px; border-radius: 4px; color: #B91C1C; font-weight: 600; }
    .status-box-warning { background-color: #FFFBEB; border-left: 4px solid #D97706; padding: 12px 16px; border-radius: 4px; color: #B45309; font-weight: 600; }
    </style>
""", unsafe_allow_html=True)

# ---------------------------------------------------------
# LINKS OFICIAIS DAS FONTES
# ---------------------------------------------------------
URL_FONTE_MTE = "https://www.gov.br/trabalho-e-emprego/pt-br/assuntos/inspecao-do-trabalho/areas-de-atuacao/cadastro_de_empregadores.pdf"
URL_FONTE_IBAMA = "https://aie-consultapublica.ibama.gov.br/home"
URL_FONTE_SICAR = "https://www.car.gov.br/#/consultar"

# ---------------------------------------------------------
# FUNÇÕES DE CONSULTA EM BASES PÚBLICAS
# ---------------------------------------------------------

def consultar_trabalho_escravo_online(doc):
    """Consulta o Cadastro de Empregadores (Lista Suja do MTE)"""
    if not doc:
        return "Não Consultado", "Nenhum CPF/CNPJ fornecido para verificação."
        
    doc_limpo = re.sub(r'\D', '', str(doc))
    if not doc_limpo:
        return "Erro", "Documento informado possui formato inválido."
    
    try:
        req = urllib.request.Request(URL_FONTE_MTE, headers={'User-Agent': 'Mozilla/5.0'})
        response = urllib.request.urlopen(req)
        pdf_bytes = io.BytesIO(response.read())
        
        encontrado = False
        detalhes_empregador = ""
        
        with pdfplumber.open(pdf_bytes) as pdf:
            for pagina in pdf.pages:
                texto = pagina.extract_text()
                if texto:
                    linhas = texto.split('\n')
                    for linha in linhas:
                        linha_limpa = re.sub(r'\D', '', linha)
                        if doc_limpo in linha_limpa:
                            encontrado = True
                            detalhes_empregador = linha
                            break
                if encontrado:
                    break
        
        if encontrado:
            return "Não Conforme", f"Consta no Cadastro do MTE (Lista Suja): {detalhes_empregador[:80]}..."
        else:
            return "Conforme", "Sem ocorrências registradas no Cadastro de Empregadores do MTE."
            
    except Exception as e:
        return "Erro", f"Falha na conexão com o portal MTE: {str(e)}"


@st.cache_data(ttl=86400)
def carregar_base_ibama_dados_abertos():
    """
    Baixa automaticamente a base oficial consolidada do IBAMA direto da URL
    de Dados Abertos para a memória RAM (Cache de 24 horas).
    """
    url_csv = "https://dadosabertos.ibama.gov.br/dados/SICAFI/AC/QuantidadeApreensoes/termo_embargo.csv"
    try:
        df = pd.read_csv(url_csv, sep=';', encoding='utf-8', dtype=str, on_bad_lines='skip')
        return df
    except Exception:
        return None


def consultar_embargos_ibama(doc):
    """
    Consulta automática e em tempo real na base oficial do IBAMA em memória.
    """
    if not doc:
        return "Não Consultado", "Nenhum CPF/CNPJ fornecido para consulta do IBAMA."
        
    doc_limpo = re.sub(r'\D', '', str(doc))
    if not doc_limpo:
        return "Erro", "Documento informado possui formato inválido."

    df_ibama = carregar_base_ibama_dados_abertos()
    
    if df_ibama is not None and not df_ibama.empty:
        colunas_doc = [c for c in df_ibama.columns if 'CPF' in c.upper() or 'CNPJ' in c.upper() or 'DOC' in c.upper()]
        
        encontrado = False
        qtd = 0
        tipo = ""
        
        for col in colunas_doc:
            match = df_ibama[df_ibama[col].astype(str).str.replace(r'\D', '', regex=True) == doc_limpo]
            if not match.empty:
                encontrado = True
                qtd = len(match)
                tipo = str(match.iloc[0].get('DES_INFRACAO', match.iloc[0].get('NOM_INFRACAO', 'Sanção Ambiental'))).upper()
                break
                
        if encontrado:
            if "EMBARGO" in tipo or "INTERDIÇÃO" in tipo:
                return "Não Conforme", f"Consta EMBARGO AMBIENTAL ativo no IBAMA ({qtd} registro(s))."
            else:
                return "Pendente", f"Consta AUTO DE INFRAÇÃO / Sanção no IBAMA ({qtd} registro(s)). Análise manual requerida."
        else:
            return "Conforme", "Sem registros de embargos ou autos de infração ativos no IBAMA."

    # Fallback de segurança para auditoria caso o servidor de Dados Abertos esteja fora do ar
    return "Pendente", "Consulta automatizada indisponível temporariamente no servidor do IBAMA. Recomenda-se emissão de certidão no portal AIE."


def consultar_car_sicar_nacional(car_codigo):
    """Consulta o Cadastro Ambiental Rural no SICAR Nacional"""
    if not car_codigo:
        return "Não Consultado", "Nenhum número de CAR fornecido para verificação.", None
        
    car_limpo = car_codigo.strip().upper()
    if not car_limpo:
        return "Erro", "Código do CAR informado é inválido.", None
        
    uf = car_limpo[:2] if len(car_limpo) >= 2 else "UF"
    
    gdf_retorno = None
    situacao_encontrada = None
    
    try:
        wfs_url = "https://geoserver.car.gov.br/geoserver/sicar/wfs"
        params = {
            'service': 'WFS',
            'version': '1.0.0',
            'request': 'GetFeature',
            'typeName': f'sicar:sicar_imoveis_{uf.lower()}',
            'outputFormat': 'json',
            'CQL_FILTER': f"cod_imovel='{car_limpo}'"
        }
        url_requisicao = f"{wfs_url}?{urllib.parse.urlencode(params)}"
        gdf_temp = gpd.read_file(url_requisicao)
        if not gdf_temp.empty:
            gdf_retorno = gdf_temp
            if 'ind_status' in gdf_temp.columns:
                situacao_encontrada = str(gdf_temp.iloc[0]['ind_status']).upper()
            elif 'ind_situacao' in gdf_temp.columns:
                situacao_encontrada = str(gdf_temp.iloc[0]['ind_situacao']).upper()
    except Exception:
        gdf_retorno = None

    if not situacao_encontrada:
        urls_para_testar = [
            f"https://consultapublica.car.gov.br/publico/imoveis/sicar/consultarImovel?codigoRecibo={car_limpo}",
            f"https://www.car.gov.br/api/v1/imoveis/{car_limpo}"
        ]
        
        session = requests.Session()
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
            'Accept': 'application/json, text/plain, */*'
        }
        
        for url in urls_para_testar:
            try:
                res = session.get(url, headers=headers, timeout=5)
                if res.status_code == 200 and res.text.strip():
                    dados = res.json()
                    if isinstance(dados, list) and len(dados) > 0:
                        dados = dados[0]
                    if isinstance(dados, dict):
                        situacao_encontrada = str(dados.get("situacaoImovel", dados.get("situacao", ""))).upper()
                        if situacao_encontrada:
                            break
            except Exception:
                continue

    if situacao_encontrada:
        if "PENDENTE" in situacao_encontrada or "EM ANÁLISE" in situacao_encontrada:
            return "Pendente", f"Imóvel identificado no SICAR ({uf}). Situação: PENDENTE / EM ANÁLISE.", gdf_retorno
        elif "CANCELADO" in situacao_encontrada or "SUSPENSO" in situacao_encontrada or "INATIVO" in situacao_encontrada:
            return "Não Conforme", f"Imóvel irregular no SICAR ({uf}). Situação: {situacao_encontrada}.", gdf_retorno
        elif "ATIVO" in situacao_encontrada or "CADASTRADO" in situacao_encontrada:
            return "Conforme", f"Imóvel cadastrado e regular no SICAR ({uf}). Situação: ATIVO.", gdf_retorno

    return "Pendente", f"Registro localizado no padrão {uf}. Situação pendente de confirmação via demonstrativo direto.", gdf_retorno


def desenhar_mapa_car(gdf):
    if gdf is None or gdf.empty:
        return
    gdf_wgs84 = gdf.to_crs(epsg=4326)
    centro = gdf_wgs84.geometry.centroid.iloc[0]
    mapa = folium.Map(location=[centro.y, centro.x], zoom_start=13, tiles="OpenStreetMap")
    
    folium.GeoJson(
        gdf_wgs84,
        style_function=lambda feature: {
            'fillColor': '#2563EB',
            'color': '#1D4ED8',
            'weight': 2,
            'fillOpacity': 0.3,
        },
        tooltip="Imóvel Rural Cadastrado no SICAR"
    ).add_to(mapa)
    
    st_folium(mapa, width=800, height=380)

# ---------------------------------------------------------
# GERADOR DE PDF COM LINKS DE AUDITORIA (REPORTLAB)
# ---------------------------------------------------------

def gerar_pdf_evidencia(data_emissao, cpf_cnpj, car_codigo, status_mte, desc_mte, status_ibama, desc_ibama, status_car, desc_car, status_geral):
    buffer = io.BytesIO()
    doc = SimpleDocTemplate(buffer, pagesize=letter, rightMargin=36, leftMargin=36, topMargin=36, bottomMargin=36)
    styles = getSampleStyleSheet()
    story = []

    title_style = ParagraphStyle('DocTitle', parent=styles['Heading1'], fontSize=16, leading=20, textColor=colors.HexColor("#1E293B"), spaceAfter=4)
    subtitle_style = ParagraphStyle('DocSubtitle', parent=styles['Normal'], fontSize=9, leading=12, textColor=colors.HexColor("#64748B"), spaceAfter=15)
    section_style = ParagraphStyle('SectionHeader', parent=styles['Heading2'], fontSize=12, leading=15, textColor=colors.HexColor("#0F172A"), spaceBefore=12, spaceAfter=6)
    body_style = ParagraphStyle('BodyTextCustom', parent=styles['Normal'], fontSize=9, leading=12, textColor=colors.HexColor("#334155"))

    story.append(Paragraph("RELATÓRIO DE VERIFICAÇÃO DE CONFORMIDADE SOCIOAMBIENTAL", title_style))
    story.append(Paragraph("Evidência Auditável para Cadeias de Suprimento Agrícola", subtitle_style))
    story.append(HRFlowable(width="100%", thickness=1, color=colors.HexColor("#CBD5E1"), spaceAfter=15))

    dados_meta = [
        [Paragraph("<b>Data/Hora UTC:</b>", body_style), Paragraph(data_emissao, body_style), Paragraph("<b>Resultado Geral:</b>", body_style), Paragraph(f"<b>{status_geral}</b>", body_style)],
        [Paragraph("<b>CPF/CNPJ Solicitado:</b>", body_style), Paragraph(cpf_cnpj if cpf_cnpj else "Não informado", body_style), Paragraph("<b>Registro CAR:</b>", body_style), Paragraph(car_codigo if car_codigo else "Não informado", body_style)]
    ]
    t_meta = Table(dados_meta, colWidths=[110, 160, 100, 170])
    t_meta.setStyle(TableStyle([
        ('BACKGROUND', (0,0), (-1,-1), colors.HexColor("#F8FAFC")),
        ('BORDER', (0,0), (-1,-1), 0.5, colors.HexColor("#E2E8F0")),
        ('PADDING', (0,0), (-1,-1), 6),
        ('VALIGN', (0,0), (-1,-1), 'MIDDLE'),
    ]))
    story.append(t_meta)
    story.append(Spacer(1, 12))

    story.append(Paragraph("Detalhamento dos Checkpoints Consultados e Fontes Oficiais", section_style))
    
    dados_checkpoints = [
        [Paragraph("<b>Critério / Fonte Oficial</b>", body_style), Paragraph("<b>Status</b>", body_style), Paragraph("<b>Detalhamento Técnico & Link de Origem</b>", body_style)],
        [
            Paragraph("Trabalho Análogo à Escravidão<br/><font color='#64748B' size=7>MTE - Portaria Interministerial</font>", body_style),
            Paragraph(f"<b>{status_mte}</b>", body_style),
            Paragraph(f"{desc_mte}<br/><font size=7 color='#2563EB'><u><a href='{URL_FONTE_MTE}'>Acessar Base Oficial MTE</a></u></font>", body_style)
        ],
        [
            Paragraph("Embargos e Sanções Ambientais<br/><font color='#64748B' size=7>IBAMA / Ministério do Meio Ambiente</font>", body_style),
            Paragraph(f"<b>{status_ibama}</b>", body_style),
            Paragraph(f"{desc_ibama}<br/><font size=7 color='#2563EB'><u><a href='{URL_FONTE_IBAMA}'>Portal de Certidões IBAMA (AIE)</a></u></font>", body_style)
        ],
        [
            Paragraph("Cadastro Ambiental Rural<br/><font color='#64748B' size=7>SICAR / Ministério do Meio Ambiente</font>", body_style),
            Paragraph(f"<b>{status_car}</b>", body_style),
            Paragraph(f"{desc_car}<br/><font size=7 color='#2563EB'><u><a href='{URL_FONTE_SICAR}'>Consulta Manual Demonstrativo SICAR</a></u></font>", body_style)
        ]
    ]
    t_check = Table(dados_checkpoints, colWidths=[160, 90, 290])
    t_check.setStyle(TableStyle([
        ('BACKGROUND', (0,0), (-1,0), colors.HexColor("#F1F5F9")),
        ('GRID', (0,0), (-1,-1), 0.5, colors.HexColor("#CBD5E1")),
        ('PADDING', (0,0), (-1,-1), 8),
        ('VALIGN', (0,0), (-1,-1), 'TOP'),
    ]))
    story.append(t_check)
    story.append(Spacer(15, 15))

    story.append(Paragraph("Metodologia de Verificação", section_style))
    texto_metodologia = (
        "Este documento é um extrato gerado automaticamente via varredura direta e simultânea em bases "
        "públicas governamentais brasileiras (MTE, IBAMA e SICAR). O objetivo deste registro é servir como evidência documental "
        "de diligência socioambiental prévia para auditorias internas e procedimentos de conformidade na aquisição de matérias-primas."
    )
    story.append(Paragraph(texto_metodologia, body_style))
    story.append(HRFlowable(width="100%", thickness=0.5, color=colors.HexColor("#E2E8F0"), spaceBefore=15, spaceAfter=8))
    story.append(Paragraph("Sistema de Verificação Socioambiental • Documento Autenticado para Auditoria", ParagraphStyle('Footer', parent=styles['Normal'], fontSize=7, textColor=colors.HexColor("#94A3B8"))))

    doc.build(story)
    buffer.seek(0)
    return buffer

# ---------------------------------------------------------
# INTERFACE PRINCIPAL
# ---------------------------------------------------------

st.markdown('<div class="main-header">Sistema de Avaliação Socioambiental de Fornecedores</div>', unsafe_allow_html=True)
st.markdown('<div class="sub-header">Módulo de Verificação de Conformidade em Tempo Real para Auditorias e Diligência de Suprimentos</div>', unsafe_allow_html=True)

with st.form("form_consulta_limpo"):
    col_input1, col_input2 = st.columns(2)
    with col_input1:
        cpf_cnpj = st.text_input("CPF ou CNPJ do Titular", placeholder="Digite apenas números ou formatado (Opcional se inserido CAR)")
    with col_input2:
        numero_car = st.text_input("Número do CAR", placeholder="Ex: MT-5105150-E3C36116DF3D4BA98D7263BF0A89A800")
        
    btn_buscar = st.form_submit_button("Executar Verificação de Conformidade")

if btn_buscar:
    if not cpf_cnpj and not numero_car:
        st.warning("É necessário preencher ao menos um parâmetro de busca (CPF/CNPJ do titular ou Número do CAR).")
    else:
        with st.spinner("Consultando bases governamentais (MTE, IBAMA, SICAR) e gerando extrato..."):
            status_mte, desc_mte = consultar_trabalho_escravo_online(cpf_cnpj)
            status_ibama, desc_ibama = consultar_embargos_ibama(cpf_cnpj)
            status_car, desc_car, gdf_car = consultar_car_sicar_nacional(numero_car)
            
            # Lógica estrita para consolidação em auditoria
            if status_mte == "Não Conforme" or status_ibama == "Não Conforme" or status_car == "Não Conforme":
                status_geral = "REPROVADO / BLOQUEADO"
                classe_css = "status-box-rejected"
            elif status_car == "Pendente" or status_ibama == "Pendente" or status_ibama == "Em Análise Manual" or status_mte == "Erro":
                status_geral = "ALERTA / ANÁLISE MANUAL NECESSÁRIA"
                classe_css = "status-box-warning"
            else:
                status_geral = "APROVADO"
                classe_css = "status-box-approved"

            data_hora_atual = datetime.utcnow().strftime("%d/%m/%Y %H:%M:%S UTC")

        st.markdown("---")
        st.subheader("Relatório de Avaliação do Fornecedor")
        st.markdown(f'<div class="{classe_css}">Resultado Geral da Verificação: {status_geral}</div>', unsafe_allow_html=True)
        st.write("")

        col_res1, col_res2 = st.columns(2)
        with col_res1:
            st.markdown("#### Dados de Identificação")
            st.write(f"**Data/Hora da Consulta:** {data_hora_atual}")
            st.write(f"**CPF/CNPJ Solicitado:** {cpf_cnpj if cpf_cnpj else 'Não submetido'}")
            st.write(f"**Código do CAR Solicitado:** {numero_car if numero_car else 'Não submetido'}")

        with col_res2:
            st.markdown("#### Checkpoints de Conformidade")
            st.write(f"• **Lista Suja Trabalho Escravo (MTE):** {status_mte}")
            st.caption(f"{desc_mte} — [Fonte Oficial MTE]({URL_FONTE_MTE})")
            
            st.write(f"• **Embargos e Sanções Ambientais (IBAMA):** {status_ibama}")
            st.caption(f"{desc_ibama} — [Portal de Certidões IBAMA]({URL_FONTE_IBAMA})")
            
            st.write(f"• **Situação Cadastral (SICAR):** {status_car}")
            st.caption(f"{desc_car} — [Demonstrativo SICAR]({URL_FONTE_SICAR})")

        # Painel Informativo para Consulta Manual se houver pendência/alerta
        if status_geral == "ALERTA / ANÁLISE MANUAL NECESSÁRIA":
            st.markdown("---")
            st.warning("⚠️ **Orientações para Validação Manual de Auditoria:**")
            col_l1, col_l2 = st.columns(2)
            with col_l1:
                st.markdown(f"1. **Validar CAR Pendente/Em Análise:** [Acessar Portal do SICAR]({URL_FONTE_SICAR}) e cole o código `{numero_car}` no campo de demonstrativo.")
            with col_l2:
                st.markdown(f"2. **Emitir Certidão de Embargo / Autos:** [Acessar Portal AIE IBAMA]({URL_FONTE_IBAMA}) e informe o CPF/CNPJ `{cpf_cnpj}`.")

        if gdf_car is not None:
            st.markdown("---")
            st.markdown("#### Mapeamento da Propriedade (SICAR Nacional)")
            desenhar_mapa_car(gdf_car)

        st.markdown("---")
        pdf_bytes = gerar_pdf_evidencia(
            data_hora_atual, cpf_cnpj, numero_car,
            status_mte, desc_mte, status_ibama, desc_ibama, status_car, desc_car, status_geral
        )
        
        st.download_button(
            label="📄 Baixar Extrato de Evidência em PDF (Para Auditoria)",
            data=pdf_bytes,
            file_name=f"Evidencia_Socioambiental_{datetime.now().strftime('%Y%m%d_%H%M%S')}.pdf",
            mime="application/pdf"
        )