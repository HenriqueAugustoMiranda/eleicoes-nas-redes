#!/usr/bin/env python3
# -*- coding: utf-8 -*-

from __future__ import annotations

import csv
import json
import re
import sqlite3
import sys
from collections import defaultdict
from datetime import date, datetime, time, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import matplotlib
matplotlib.use("Agg")  # Permite executar sem ambiente gráfico.
import matplotlib.pyplot as plt
from matplotlib.backends.backend_pdf import PdfPages


# -----------------------------------------------------------------------------
# CONFIGURAÇÕES
# -----------------------------------------------------------------------------

PASTA_PROJETO = Path(__file__).resolve().parent.parent
BANCO_DADOS = PASTA_PROJETO / "db" / "whatsapp.sqlite"
PASTA_SAIDA = PASTA_PROJETO / "exports" / "graficos_artigo"

DATA_INICIAL = date(2026, 9, 4)
FUSO_HORARIO = ZoneInfo("America/Sao_Paulo")

# Estes tipos representam mensagens/eventos, e não mídias.
TIPOS_NAO_MIDIA = {
    # chat e text são incluídos como "Mensagens de texto" nos gráficos.
    "revoked",
    "protocol",
    "notification",
    "e2e_notification",
}

# Nomes mais legíveis para os tipos mais comuns. Tipos não listados continuam
# aparecendo nos gráficos com seu nome original normalizado.
ROTULOS_TIPOS = {
    "chat": "Mensagens de texto",
    "text": "Mensagens de texto",
    "image": "Imagens",
    "video": "Vídeos",
    "ptv": "Vídeos redondos",
    "audio": "Áudios",
    "ptt": "Áudios de voz",
    "document": "Documentos",
    "sticker": "Figurinhas",
    "contact": "Contatos",
    "contacts": "Contatos",
    "location": "Localizações",
    "live_location": "Localizações ao vivo",
    "gif": "GIFs",
    "links": "Links",
}

# Paleta com contraste adequado para gráficos científicos.
CORES_CANAIS = ["#245A81", "#D17A32", "#3A806B", "#8B659D", "#B84A4A"]
CORES_TIPOS = [
    "#245A81", "#D17A32", "#3A806B", "#8B659D", "#B84A4A",
    "#6C8EAD", "#B59A42", "#59636E", "#C4778A", "#5D9C9A",
    "#9A795D", "#7D88B8",
]

# Expressões reconhecem URLs http(s) e endereços iniciados por www.
REGEX_URL = re.compile(r"(?:https?://|www\.)[^\s<>\"'\\]+", re.IGNORECASE)
PONTUACAO_FINAL_URL = ".,;:!?)]}»”’"


# -----------------------------------------------------------------------------
# NORMALIZAÇÃO E LEITURA DOS DADOS
# -----------------------------------------------------------------------------

def normalizar_tipo(tipo: object) -> str:
    """Normaliza o nome do tipo para facilitar filtros e agrupamentos."""
    if tipo is None:
        return ""
    return str(tipo).strip().lower().replace(" ", "_").replace("-", "_")


def rotulo_tipo(tipo: str) -> str:
    """Retorna o rótulo de apresentação do tipo."""
    if tipo in ROTULOS_TIPOS:
        return ROTULOS_TIPOS[tipo]
    return tipo.replace("_", " ").strip().capitalize() or "Não identificado"


def excluir_tipo_midia(tipo: object) -> bool:
    """Indica se o tipo deve ficar fora dos gráficos de mídia."""
    tipo_normalizado = normalizar_tipo(tipo)

    if not tipo_normalizado:
        return True
    if tipo_normalizado in TIPOS_NAO_MIDIA:
        return True
    if tipo_normalizado.startswith("unknown"):
        return True
    if "album" in tipo_normalizado:
        return True
    # Links são contados separadamente, pela quantidade de URLs da coluna links.
    if tipo_normalizado in {"link", "links"}:
        return True

    return False


def converter_data_mensagem(timestamp: object, data_texto: object = None) -> date | None:
    """Converte timestamp Unix ou coluna data para uma data local de São Paulo."""
    if timestamp is not None and str(timestamp).strip() != "":
        try:
            valor = float(timestamp)
            # Aceita timestamps em segundos ou milissegundos.
            if abs(valor) >= 10_000_000_000:
                valor /= 1000.0
            return datetime.fromtimestamp(valor, tz=FUSO_HORARIO).date()
        except (TypeError, ValueError, OverflowError, OSError):
            pass

    if data_texto is None:
        return None

    texto = str(data_texto).strip()
    if not texto:
        return None

    # ISO 8601, com ou sem fuso horário.
    try:
        convertido = datetime.fromisoformat(texto.replace("Z", "+00:00"))
        if convertido.tzinfo is None:
            convertido = convertido.replace(tzinfo=FUSO_HORARIO)
        return convertido.astimezone(FUSO_HORARIO).date()
    except ValueError:
        pass

    # Formatos comuns em exportações locais.
    formatos = (
        "%d/%m/%Y %H:%M:%S",
        "%d/%m/%Y %H:%M",
        "%d/%m/%Y",
        "%Y-%m-%d %H:%M:%S",
        "%Y-%m-%d",
    )
    for formato in formatos:
        try:
            convertido = datetime.strptime(texto, formato)
            convertido = convertido.replace(tzinfo=FUSO_HORARIO)
            return convertido.date()
        except ValueError:
            continue

    return None


def _urls_em_texto(texto: str) -> list[str]:
    """Extrai ocorrências de URLs de um texto, removendo pontuação final."""
    texto = texto.replace("\\/", "/")
    encontradas = []
    for correspondencia in REGEX_URL.findall(texto):
        url = correspondencia.rstrip(PONTUACAO_FINAL_URL)
        if url:
            encontradas.append(url)
    return encontradas


def contar_links(valor: object) -> int:
    """Conta links em JSON, listas, objetos ou texto simples.

    A coluna mensagens.links pode variar de formato conforme a versão da coleta.
    Esta função aceita:
      - JSON em lista, por exemplo ["https://a.com", "https://b.com"];
      - JSON em objeto, por exemplo {"url": "https://a.com"};
      - texto contendo uma ou mais URLs;
      - lista/objeto já decodificados.

    Cada ocorrência é contabilizada. URLs repetidas também contam novamente,
    pois podem representar links distintos publicados em uma mensagem.
    """
    if valor is None:
        return 0

    if isinstance(valor, bytes):
        try:
            valor = valor.decode("utf-8", errors="replace")
        except Exception:
            return 0

    if isinstance(valor, str):
        texto = valor.strip()
        if not texto:
            return 0
        try:
            estrutura = json.loads(texto)
        except (json.JSONDecodeError, TypeError):
            return len(_urls_em_texto(texto))
        return contar_links(estrutura)

    if isinstance(valor, (list, tuple, set)):
        return sum(contar_links(item) for item in valor)

    if isinstance(valor, dict):
        # Quando há um campo explicitamente destinado a URLs, prioriza-o para
        # não contar novamente metadados do mesmo link (título, descrição etc.).
        chaves_url = ("links", "url", "href", "link", "uri")
        for chave in chaves_url:
            if chave in valor:
                return contar_links(valor[chave])

        # Alguns formatos guardam os dados em objetos sem chave URL explícita.
        return sum(contar_links(item) for item in valor.values())

    # Um valor escalar isolado não é presumido como link.
    return 0


def carregar_dados():
    """Lê mensagens e canais, aplicando o corte temporal."""
    if not BANCO_DADOS.exists():
        raise FileNotFoundError(
            f"Banco de dados não encontrado: {BANCO_DADOS}\n"
            "Confira se o SSD está montado e se o caminho do projeto está correto."
        )

    with sqlite3.connect(BANCO_DADOS) as conexao:
        conexao.row_factory = sqlite3.Row

        # O nome vem da tabela canais; se não houver correspondência, usa o ID.
        canais = {
            str(linha["id"]): (linha["nome"] or str(linha["id"]))
            for linha in conexao.execute("SELECT id, nome FROM canais")
        }

        consulta = """
            SELECT
                m.canal_id,
                m.timestamp,
                m.data,
                m.tipo,
                m.links
            FROM mensagens AS m
            WHERE m.canal_id IS NOT NULL
        """
        linhas = conexao.execute(consulta).fetchall()

    mensagens = []
    datas_validas = []
    canais_encontrados = set()
    ignoradas_sem_data = 0
    anteriores_ao_corte = 0

    for linha in linhas:
        dia = converter_data_mensagem(linha["timestamp"], linha["data"])
        if dia is None:
            ignoradas_sem_data += 1
            continue
        if dia < DATA_INICIAL:
            anteriores_ao_corte += 1
            continue

        canal_id = str(linha["canal_id"])
        canal_nome = canais.get(canal_id, canal_id)
        tipo = normalizar_tipo(linha["tipo"])
        quantidade_links = contar_links(linha["links"])

        mensagens.append({
            "canal_id": canal_id,
            "canal_nome": canal_nome,
            "data": dia,
            "tipo": tipo,
            "quantidade_links": quantidade_links,
        })
        datas_validas.append(dia)
        canais_encontrados.add(canal_id)

    if not mensagens:
        raise RuntimeError(
            "Nenhuma mensagem encontrada a partir de 04/09/2026. "
            "Verifique os campos timestamp/data do banco."
        )

    # Ordena pelo nome para manter a mesma ordem em todas as figuras e CSVs.
    lista_canais = sorted(
        canais_encontrados,
        key=lambda canal_id: str(canais.get(canal_id, canal_id)).casefold(),
    )
    nomes_canais = {
        canal_id: str(canais.get(canal_id, canal_id))
        for canal_id in lista_canais
    }

    estatisticas_leitura = {
        "total_linhas": len(linhas),
        "mensagens_analisadas": len(mensagens),
        "anteriores_ao_corte": anteriores_ao_corte,
        "sem_data_valida": ignoradas_sem_data,
        "data_final": max(datas_validas),
    }

    return mensagens, lista_canais, nomes_canais, estatisticas_leitura


# -----------------------------------------------------------------------------
# AGREGAÇÃO DIÁRIA
# -----------------------------------------------------------------------------

def gerar_serie_de_dias(data_final: date) -> list[date]:
    quantidade = (data_final - DATA_INICIAL).days + 1
    return [date.fromordinal(DATA_INICIAL.toordinal() + i) for i in range(quantidade)]


def agregar_dados(mensagens, lista_canais, dias):
    """Agrega volume de mensagens e quantidades por tipo de mídia/link."""
    volume = {
        canal_id: defaultdict(int)
        for canal_id in lista_canais
    }
    midias = {
        canal_id: defaultdict(lambda: defaultdict(int))
        for canal_id in lista_canais
    }
    totais_midias = {
        canal_id: defaultdict(int)
        for canal_id in lista_canais
    }

    # Garante que "Links" apareça em todos os canais, inclusive quando a
    # quantidade encontrada no período for zero.
    for canal_id in lista_canais:
        midias[canal_id]["Links"]
        totais_midias[canal_id]["Links"] = 0

    for mensagem in mensagens:
        canal_id = mensagem["canal_id"]
        dia = mensagem["data"]
        tipo = mensagem["tipo"]

        # Volume inclui toda publicação registrada, inclusive revoked, unknown
        # e eventos que não são mídia. O filtro de mídia não altera este gráfico.
        volume[canal_id][dia] += 1

        # Links: contabiliza a quantidade de URLs, não a quantidade de mensagens.
        qtd_links = mensagem["quantidade_links"]
        if qtd_links > 0:
            midias[canal_id]["Links"][dia] += qtd_links
            totais_midias[canal_id]["Links"] += qtd_links

        # Inclui mensagens textuais (chat/text) e mídias; ignora eventos,
        # unknown* e álbuns. chat e text compartilham o mesmo rótulo.
        if not excluir_tipo_midia(tipo):
            rotulo = rotulo_tipo(tipo)
            midias[canal_id][rotulo][dia] += 1
            totais_midias[canal_id][rotulo] += 1

    return volume, midias, totais_midias


# -----------------------------------------------------------------------------
# ESTILO E FUNÇÕES DOS GRÁFICOS
# -----------------------------------------------------------------------------

def configurar_estilo():
    plt.rcParams.update({
        "font.family": "DejaVu Sans",
        "font.size": 9,
        "axes.titlesize": 13,
        "axes.titleweight": "semibold",
        "axes.labelsize": 10,
        "axes.labelcolor": "#30343B",
        "xtick.labelsize": 7,
        "ytick.labelsize": 8,
        "legend.fontsize": 8,
        "figure.titlesize": 15,
        "figure.titleweight": "semibold",
        "text.color": "#252A31",
        "axes.edgecolor": "#AAB2BA",
        "axes.linewidth": 0.8,
        "axes.spines.top": False,
        "axes.spines.right": False,
        "axes.grid": True,
        "grid.color": "#D9DEE3",
        "grid.linewidth": 0.65,
        "grid.alpha": 0.8,
        "axes.axisbelow": True,
        "savefig.facecolor": "white",
        "figure.facecolor": "white",
    })


def tamanho_figura_temporal(numero_dias: int, altura: float = 6.0):
    # A largura cresce com a quantidade de dias para permitir a leitura diária.
    largura = max(15.0, min(30.0, 8.0 + numero_dias * 0.23))
    return largura, altura


def configurar_eixo_temporal(ax, dias: list[date]):
    datas_datetime = [datetime.combine(dia, time.min) for dia in dias]
    ax.set_xticks(datas_datetime)
    ax.set_xticklabels(
        [dia.strftime("%d/%m") for dia in dias],
        rotation=65,
        ha="right",
        rotation_mode="anchor",
    )
    ax.set_xlim(
        datetime.combine(dias[0], time.min) - timedelta(hours=12),
        datetime.combine(dias[-1], time.min) + timedelta(hours=18),
    )
    ax.set_xlabel("Data (dia/mês)", labelpad=10)
    ax.grid(axis="y")
    ax.grid(axis="x", alpha=0.18, linewidth=0.45)


def salvar_figura(figura, nome_arquivo: str):
    caminho_png = PASTA_SAIDA / f"{nome_arquivo}.png"
    caminho_pdf = PASTA_SAIDA / f"{nome_arquivo}.pdf"
    figura.savefig(caminho_png, dpi=300, bbox_inches="tight")
    figura.savefig(caminho_pdf, bbox_inches="tight")
    return caminho_png, caminho_pdf


def grafico_volume_mensagens(dias, lista_canais, nomes_canais, volume):
    largura, altura = tamanho_figura_temporal(len(dias), 6.5)
    figura, ax = plt.subplots(figsize=(largura, altura))

    x = [datetime.combine(dia, time.min) for dia in dias]
    for indice, canal_id in enumerate(lista_canais):
        valores = [volume[canal_id].get(dia, 0) for dia in dias]
        ax.plot(
            x,
            valores,
            label=nomes_canais[canal_id],
            color=CORES_CANAIS[indice % len(CORES_CANAIS)],
            linewidth=2.0,
            marker="o",
            markersize=3.0,
        )

    ax.set_title("Volume diário de mensagens por canal", loc="left", pad=14)
    ax.set_ylabel("Quantidade de mensagens")
    configurar_eixo_temporal(ax, dias)
    ax.legend(title="Canal", frameon=False, loc="upper left", ncol=1)
    figura.text(
        0.01, 0.015,
        "Nota: todas as mensagens registradas são contabilizadas, independentemente do tipo.",
        fontsize=8, color="#59636E",
    )
    figura.tight_layout(rect=(0, 0.045, 1, 1))
    return figura


def grafico_midias_canal(dias, canal_id, canal_nome, midias_canal):
    largura, altura = tamanho_figura_temporal(len(dias), 7.0)
    figura, ax = plt.subplots(figsize=(largura, altura))

    tipos = sorted(
        midias_canal.keys(),
        key=lambda tipo: sum(midias_canal[tipo].values()),
        reverse=True,
    )

    x = [datetime.combine(dia, time.min) for dia in dias]
    for indice, tipo in enumerate(tipos):
        valores = [midias_canal[tipo].get(dia, 0) for dia in dias]
        ax.plot(
            x,
            valores,
            label=tipo,
            color=CORES_TIPOS[indice % len(CORES_TIPOS)],
            linewidth=1.8,
            marker="o",
            markersize=2.7,
        )

    ax.set_title(f"Distribuição temporal de mídias e links — {canal_nome}", loc="left", pad=14)
    ax.set_ylabel("Quantidade por dia")
    configurar_eixo_temporal(ax, dias)

    if tipos:
        colunas_legenda = 2 if len(tipos) > 7 else 1
        ax.legend(
            title="Tipo",
            frameon=False,
            loc="upper left",
            ncol=colunas_legenda,
        )
    else:
        ax.text(
            0.5, 0.5, "Nenhuma mídia ou URL encontrada no período.",
            transform=ax.transAxes, ha="center", va="center",
        )

    figura.text(
        0.01, 0.015,
        "Nota: unknown* e tipos de álbum foram excluídos; links representam URLs individuais.",
        fontsize=8, color="#59636E",
    )
    figura.tight_layout(rect=(0, 0.045, 1, 1))
    return figura


def grafico_comparacao_midias(lista_canais, nomes_canais, totais_midias):
    tipos = sorted(
        {tipo for canal_id in lista_canais for tipo in totais_midias[canal_id]},
        key=lambda tipo: sum(totais_midias[c][tipo] for c in lista_canais),
        reverse=True,
    )

    if not tipos:
        tipos = ["Sem dados"]

    numero_tipos = len(tipos)
    largura = max(10.0, min(22.0, 7.0 + numero_tipos * 0.75))
    figura, ax = plt.subplots(figsize=(largura, 7.0))

    quantidade_canais = max(1, len(lista_canais))
    largura_barra = min(0.8 / quantidade_canais, 0.34)
    posicoes = list(range(numero_tipos))

    for indice, canal_id in enumerate(lista_canais):
        deslocamento = (indice - (quantidade_canais - 1) / 2) * largura_barra
        valores = [totais_midias[canal_id].get(tipo, 0) for tipo in tipos]
        barras = ax.bar(
            [pos + deslocamento for pos in posicoes],
            valores,
            width=largura_barra * 0.92,
            label=nomes_canais[canal_id],
            color=CORES_CANAIS[indice % len(CORES_CANAIS)],
            edgecolor="white",
            linewidth=0.5,
        )
        if numero_tipos <= 12:
            ax.bar_label(barras, padding=2, fontsize=7, rotation=0)

    ax.set_title("Comparação da quantidade de mídias e links por canal", loc="left", pad=14)
    ax.set_ylabel("Quantidade no período")
    ax.set_xlabel("Tipo")
    ax.set_xticks(posicoes)
    ax.set_xticklabels(tipos, rotation=35, ha="right")
    ax.grid(axis="y")
    ax.grid(axis="x", visible=False)
    ax.legend(title="Canal", frameon=False)

    figura.text(
        0.01, 0.015,
        "Nota: links são contados por URL; unknown* e tipos de álbum foram excluídos.",
        fontsize=8, color="#59636E",
    )
    figura.tight_layout(rect=(0, 0.045, 1, 1))
    return figura


# -----------------------------------------------------------------------------
# EXPORTAÇÃO DOS DADOS AGREGADOS
# -----------------------------------------------------------------------------

def exportar_csv(dias, lista_canais, nomes_canais, volume, midias, totais_midias):
    PASTA_SAIDA.mkdir(parents=True, exist_ok=True)

    # Volume diário em formato largo: uma coluna por canal.
    caminho_volume = PASTA_SAIDA / "volume_mensagens_diario.csv"
    with caminho_volume.open("w", newline="", encoding="utf-8-sig") as arquivo:
        escritor = csv.writer(arquivo, delimiter=";")
        escritor.writerow(["data"] + [nomes_canais[c] for c in lista_canais])
        for dia in dias:
            escritor.writerow(
                [dia.strftime("%Y-%m-%d")]
                + [volume[c].get(dia, 0) for c in lista_canais]
            )

    # Mídias e links em formato longo, adequado para análises posteriores.
    caminho_diario = PASTA_SAIDA / "tipos_midia_diario.csv"
    with caminho_diario.open("w", newline="", encoding="utf-8-sig") as arquivo:
        escritor = csv.writer(arquivo, delimiter=";")
        escritor.writerow(["data", "canal_id", "canal", "tipo", "quantidade"])
        for canal_id in lista_canais:
            for tipo in sorted(midias[canal_id]):
                for dia in dias:
                    quantidade = midias[canal_id][tipo].get(dia, 0)
                    escritor.writerow([
                        dia.strftime("%Y-%m-%d"),
                        canal_id,
                        nomes_canais[canal_id],
                        tipo,
                        quantidade,
                    ])

    caminho_resumo = PASTA_SAIDA / "resumo_tipos_midia.csv"
    with caminho_resumo.open("w", newline="", encoding="utf-8-sig") as arquivo:
        escritor = csv.writer(arquivo, delimiter=";")
        escritor.writerow(["canal_id", "canal", "tipo", "quantidade"])
        for canal_id in lista_canais:
            for tipo, quantidade in sorted(
                totais_midias[canal_id].items(), key=lambda item: item[0]
            ):
                escritor.writerow([canal_id, nomes_canais[canal_id], tipo, quantidade])

    return caminho_volume, caminho_diario, caminho_resumo


# -----------------------------------------------------------------------------
# EXECUÇÃO
# -----------------------------------------------------------------------------

def main():
    configurar_estilo()
    PASTA_SAIDA.mkdir(parents=True, exist_ok=True)

    print("=" * 72)
    print("GERAÇÃO DE GRÁFICOS — CANAIS DO WHATSAPP")
    print("=" * 72)
    print(f"Banco: {BANCO_DADOS}")
    print(f"Período: {DATA_INICIAL.strftime('%d/%m/%Y')} em diante")
    print("Tipos incluídos: mensagens de texto (chat/text), mídias conhecidas e links.")
    print("Tipos excluídos: unknown*, álbuns e eventos (revoked/protocol/notifications).")
    print("Links: quantidade de URLs individuais na coluna mensagens.links.")
    print()

    mensagens, lista_canais, nomes_canais, estatisticas = carregar_dados()
    dias = gerar_serie_de_dias(estatisticas["data_final"])
    volume, midias, totais_midias = agregar_dados(mensagens, lista_canais, dias)

    print("RESUMO DA LEITURA")
    print(f"  Linhas lidas do banco: {estatisticas['total_linhas']:,}".replace(",", "."))
    print(f"  Mensagens no período: {estatisticas['mensagens_analisadas']:,}".replace(",", "."))
    print(f"  Mensagens anteriores a 04/09/2026: {estatisticas['anteriores_ao_corte']:,}".replace(",", "."))
    print(f"  Registros sem data válida: {estatisticas['sem_data_valida']:,}".replace(",", "."))
    print(f"  Dias exibidos: {len(dias)}")
    print()

    figuras = []
    arquivos_gerados = []

    # Figura 1 — volume diário de mensagens de todos os canais.
    fig = grafico_volume_mensagens(dias, lista_canais, nomes_canais, volume)
    arquivos_gerados.extend(salvar_figura(fig, "figura_01_volume_mensagens"))
    figuras.append(fig)

    # Figura 2 em diante — uma série temporal de mídia para cada canal.
    numero_figura = 2
    for canal_id in lista_canais:
        fig = grafico_midias_canal(
            dias,
            canal_id,
            nomes_canais[canal_id],
            midias[canal_id],
        )
        nome_arquivo = f"figura_{numero_figura:02d}_midias_canal_{numero_figura - 1:02d}"
        arquivos_gerados.extend(salvar_figura(fig, nome_arquivo))
        figuras.append(fig)
        numero_figura += 1

    # Última figura — comparação total entre canais.
    fig = grafico_comparacao_midias(lista_canais, nomes_canais, totais_midias)
    arquivos_gerados.extend(salvar_figura(fig, "figura_comparacao_tipos_midia"))
    figuras.append(fig)

    # PDF único com todas as figuras, além dos PDFs individuais.
    caminho_pdf_completo = PASTA_SAIDA / "figuras_artigo_completo.pdf"
    with PdfPages(caminho_pdf_completo) as pdf:
        for figura in figuras:
            pdf.savefig(figura, bbox_inches="tight")

    for figura in figuras:
        plt.close(figura)

    csvs = exportar_csv(
        dias,
        lista_canais,
        nomes_canais,
        volume,
        midias,
        totais_midias,
    )

    print("CANAIS ANALISADOS")
    for canal_id in lista_canais:
        total_mensagens = sum(volume[canal_id].values())
        total_links = totais_midias[canal_id].get("Links", 0)
        print(f"  - {nomes_canais[canal_id]}: {total_mensagens:,} mensagens; {total_links:,} links".replace(",", "."))

    print()
    print("ARQUIVOS GERADOS")
    for caminho in arquivos_gerados:
        print(f"  {caminho}")
    print(f"  {caminho_pdf_completo}")
    for caminho in csvs:
        print(f"  {caminho}")

    print()
    print("Concluído.")
    print("=" * 72)


if __name__ == "__main__":
    try:
        main()
    except Exception as erro:
        print(f"\nERRO: {erro}", file=sys.stderr)
        sys.exit(1)
