import sqlite3
import re
from pathlib import Path
from collections import defaultdict

ROOT = Path(__file__).resolve().parent.parent
DB = ROOT / "db" / "whatsapp.sqlite"

PASTAS = [
    ROOT / "tests" / "outputs",
    ROOT / "exports",
    ROOT / "logs",
]

EXTENSOES = {
    ".json", ".jsonl", ".csv",
    ".txt", ".log", ".ndjson"
}

con = sqlite3.connect(DB)

pendentes = con.execute("""
    SELECT canal_id, mensagem_id, data, tipo
    FROM mensagens
    WHERE encaminhamentos IS NULL
    ORDER BY timestamp
""").fetchall()

con.close()

print("=" * 65)
print("BUSCA DE ENCAMINHAMENTOS HISTÓRICOS")
print("=" * 65)

arquivos = []

for pasta in PASTAS:
    if not pasta.exists():
        continue

    for arquivo in pasta.rglob("*"):
        if not arquivo.is_file():
            continue
        if arquivo.suffix.lower() not in EXTENSOES:
            continue
        if arquivo.stat().st_size > 200 * 1024 * 1024:
            continue

        arquivos.append(arquivo)

print("Arquivos examinados:", len(arquivos))
print("Mensagens sem encaminhamentos:", len(pendentes))

encontrados = defaultdict(list)

padrao = re.compile(
    r'"?(?:forwardsCount|forwards_count|encaminhamentos)"?'
    r'\s*:\s*(\d+)',
    re.IGNORECASE
)

for arquivo in arquivos:
    try:
        conteudo = arquivo.read_text(
            encoding="utf-8",
            errors="ignore"
        )
    except OSError:
        continue

    for canal, mensagem_id, data, tipo in pendentes:
        identificador = mensagem_id.rsplit("_", 1)[-1]

        pos = conteudo.find(identificador)

        if pos < 0:
            continue

        inicio = max(0, pos - 5000)
        fim = min(len(conteudo), pos + 10000)

        trecho = conteudo[inicio:fim]

        match = padrao.search(trecho)

        encontrados[mensagem_id].append({
            "arquivo": str(arquivo.relative_to(ROOT)),
            "quantidade": match.group(1) if match else None
        })

print("\n" + "=" * 65)
print("RESULTADOS")
print("=" * 65)

recuperaveis = 0
apenas_id = 0

for canal, mensagem_id, data, tipo in pendentes:
    achados = encontrados.get(mensagem_id, [])

    for item in achados:
        if item["quantidade"] is not None:
            recuperaveis += 1
            print(
                f"RECUPERÁVEL | {data} | {tipo} | "
                f"{item['quantidade']} | {item['arquivo']}"
            )
        else:
            apenas_id += 1
            print(
                f"ID ENCONTRADO, SEM CONTAGEM | {data} | "
                f"{tipo} | {item['arquivo']}"
            )

print("\n" + "=" * 65)
print("RESUMO")
print("=" * 65)
print("Mensagens com contagem histórica encontrada:", recuperaveis)
print("Ocorrências apenas com ID:", apenas_id)
print("Mensagens sem correspondência em arquivos:", 
      len(pendentes) - len(encontrados))
