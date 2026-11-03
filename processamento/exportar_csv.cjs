
const { DatabaseSync } = require('node:sqlite');
const fs = require('node:fs');
const path = require('node:path');

const DATA_DIR = process.env.WA_DATA_DIR ||
    path.resolve(__dirname, '..');

const DB_PATH = path.join(DATA_DIR, 'db', 'whatsapp.sqlite');
const CSV_PATH = path.join(DATA_DIR, 'exports', 'mensagens.csv');

if (!fs.existsSync(DB_PATH)) {
    console.error('Banco de dados não encontrado.');
    process.exit(1);
}

fs.mkdirSync(path.dirname(CSV_PATH), { recursive: true });

const db = new DatabaseSync(DB_PATH);

const registros = db.prepare(`
    SELECT
        c.nome AS canal,
        m.canal_id,
        m.mensagem_id,
        m.timestamp,
        m.data,
        m.tipo,
        m.texto,
        m.possui_midia
    FROM mensagens m
    JOIN canais c ON c.id = m.canal_id
    ORDER BY m.timestamp ASC
`).all();

db.close();

function csv(valor) {
    if (valor === null || valor === undefined) {
        return '""';
    }

    return '"' + String(valor).replace(/"/g, '""') + '"';
}

const cabecalho = [
    'canal',
    'canal_id',
    'mensagem_id',
    'timestamp',
    'data',
    'tipo',
    'texto',
    'possui_midia'
];

const linhas = [
    cabecalho,
    ...registros.map(registro => [
        registro.canal,
        registro.canal_id,
        registro.mensagem_id,
        registro.timestamp,
        registro.data,
        registro.tipo,
        registro.texto,
        registro.possui_midia
    ])
];

const conteudo = '\uFEFF' +
    linhas.map(linha => linha.map(csv).join(';')).join('\r\n');

fs.writeFileSync(CSV_PATH, conteudo, 'utf8');

console.log('Exportação concluída.');
console.log('Registros:', registros.length);
console.log('Arquivo:', CSV_PATH);
