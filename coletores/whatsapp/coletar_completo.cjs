
const { Client, LocalAuth } = require('whatsapp-web.js');
const { DatabaseSync } = require('node:sqlite');
const fs = require('node:fs');
const path = require('node:path');
const crypto = require('node:crypto');

const ROOT_DIR = path.resolve(__dirname, '../..');

const DATA_DIR = process.env.WA_DATA_DIR || ROOT_DIR;

const CONFIG = path.join(ROOT_DIR, 'config', 'canais.json');
const DB_PATH = path.join(DATA_DIR, 'db/whatsapp.sqlite');
const MEDIA_DIR = path.join(DATA_DIR, 'midias');

const PAUSA_DOWNLOAD = 500;
const EXTENSOES = {
    'image/jpeg': '.jpg',
    'image/png': '.png',
    'image/webp': '.webp',
    'video/mp4': '.mp4',
    'video/webm': '.webm',
    'audio/ogg': '.ogg',
    'audio/mpeg': '.mp3',
    'audio/mp4': '.m4a',
    'audio/wav': '.wav',
    'application/pdf': '.pdf',
    'application/zip': '.zip',
    'application/msword': '.doc',
    'application/vnd.openxmlformats-officedocument.wordprocessingml.document': '.docx',
    'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet': '.xlsx'
};

if (!fs.existsSync('/media/hen/ssd')) {
    throw new Error('SSD secundário não está disponível.');
}

if (!fs.existsSync(CONFIG)) {
    throw new Error('Arquivo config/canais.json não encontrado.');
}

const canais = JSON.parse(fs.readFileSync(CONFIG, 'utf8'));

fs.mkdirSync(path.dirname(DB_PATH), { recursive: true });
fs.mkdirSync(MEDIA_DIR, { recursive: true });

const db = new DatabaseSync(DB_PATH);

db.exec(`
    PRAGMA journal_mode = WAL;
    PRAGMA synchronous = NORMAL;
    PRAGMA busy_timeout = 5000;

    CREATE TABLE IF NOT EXISTS canais (
        id TEXT PRIMARY KEY,
        nome TEXT NOT NULL,
        ultima_coleta TEXT,
        primeira_publicacao TEXT,
        ultima_publicacao TEXT,
        mensagens_coletadas INTEGER DEFAULT 0
    );

    CREATE TABLE IF NOT EXISTS mensagens (
        canal_id TEXT NOT NULL,
        mensagem_id TEXT NOT NULL,
        timestamp INTEGER,
        data TEXT,
        tipo TEXT,
        texto TEXT,
        possui_midia INTEGER DEFAULT 0,
        PRIMARY KEY (canal_id, mensagem_id)
    );

    CREATE INDEX IF NOT EXISTS idx_mensagens_timestamp
    ON mensagens(timestamp);

    CREATE TABLE IF NOT EXISTS midias (
        canal_id TEXT NOT NULL,
        mensagem_id TEXT NOT NULL,
        tipo TEXT,
        mimetype TEXT,
        nome_original TEXT,
        caminho TEXT,
        tamanho_bytes INTEGER,
        sha256 TEXT,
        status TEXT NOT NULL,
        erro TEXT,
        baixado_em TEXT,
        tentativas INTEGER DEFAULT 0,
        atualizado_em TEXT,
        PRIMARY KEY (canal_id, mensagem_id)
    );

    CREATE TABLE IF NOT EXISTS coletas (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        canal_id TEXT NOT NULL,
        iniciada_em TEXT,
        finalizada_em TEXT,
        mensagens_retornadas INTEGER,
        mensagens_unicas INTEGER,
        primeira_publicacao TEXT,
        ultima_publicacao TEXT,
        midias_encontradas INTEGER,
        erro TEXT
    );
`);

function garantirColuna(tabela, coluna, definicao) {
    const colunas = db.prepare(
        `PRAGMA table_info(${tabela})`
    ).all().map(c => c.name);

    if (!colunas.includes(coluna)) {
        db.exec(
            `ALTER TABLE ${tabela} ADD COLUMN ${coluna} ${definicao}`
        );
    }
}

garantirColuna('canais', 'primeira_publicacao', 'TEXT');
garantirColuna('canais', 'ultima_publicacao', 'TEXT');
garantirColuna('canais', 'mensagens_coletadas', 'INTEGER DEFAULT 0');
garantirColuna('midias', 'tentativas', 'INTEGER DEFAULT 0');
garantirColuna('midias', 'atualizado_em', 'TEXT');

const inserirMensagem = db.prepare(`
    INSERT INTO mensagens (
        canal_id, mensagem_id, timestamp,
        data, tipo, texto, possui_midia
    )
    VALUES (?, ?, ?, ?, ?, ?, ?)
    ON CONFLICT(canal_id, mensagem_id) DO UPDATE SET
        timestamp = excluded.timestamp,
        data = excluded.data,
        tipo = excluded.tipo,
        texto = excluded.texto,
        possui_midia = excluded.possui_midia
`);

const inserirCanal = db.prepare(`
    INSERT INTO canais (id, nome)
    VALUES (?, ?)
    ON CONFLICT(id) DO UPDATE SET nome = excluded.nome
`);

const consultarMidia = db.prepare(`
    SELECT * FROM midias
    WHERE canal_id = ? AND mensagem_id = ?
`);

const inserirMidia = db.prepare(`
    INSERT INTO midias (
        canal_id, mensagem_id, tipo, mimetype,
        nome_original, status, erro, atualizado_em
    )
    VALUES (?, ?, ?, ?, ?, ?, NULL, ?)
    ON CONFLICT(canal_id, mensagem_id) DO UPDATE SET
        tipo = excluded.tipo,
        mimetype = excluded.mimetype,
        nome_original = COALESCE(
            excluded.nome_original,
            midias.nome_original
        ),
        status = excluded.status,
        erro = CASE
            WHEN excluded.status = 'pendente' THEN NULL
            ELSE midias.erro
        END,
        atualizado_em = excluded.atualizado_em
`);

const atualizarMidia = db.prepare(`
    UPDATE midias SET
        mimetype = ?,
        nome_original = ?,
        caminho = ?,
        tamanho_bytes = ?,
        sha256 = ?,
        status = ?,
        erro = ?,
        baixado_em = ?,
        tentativas = ?,
        atualizado_em = ?
    WHERE canal_id = ? AND mensagem_id = ?
`);

const atualizarStatus = db.prepare(`
    UPDATE midias SET
        status = ?,
        erro = ?,
        tentativas = ?,
        atualizado_em = ?
    WHERE canal_id = ? AND mensagem_id = ?
`);

const atualizarCanal = db.prepare(`
    UPDATE canais SET
        ultima_coleta = ?,
        primeira_publicacao = ?,
        ultima_publicacao = ?,
        mensagens_coletadas = ?
    WHERE id = ?
`);

const inserirColeta = db.prepare(`
    INSERT INTO coletas (
        canal_id, iniciada_em, finalizada_em,
        mensagens_retornadas, mensagens_unicas,
        primeira_publicacao, ultima_publicacao,
        midias_encontradas, erro
    )
    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
`);

function idMensagem(m) {
    return m.id?._serialized ??
        m.id?.$1 ??
        null;
}

function slug(texto) {
    return String(texto)
        .normalize('NFD')
        .replace(/[\u0300-\u036f]/g, '')
        .toLowerCase()
        .replace(/[^a-z0-9_-]+/g, '_')
        .replace(/^_+|_+$/g, '');
}

function classificar(tipo, mimetype) {
    const mime = String(mimetype || '')
        .split(';')[0]
        .trim()
        .toLowerCase();

    if (tipo === 'ptv') return 'ignorado_ptv';
    if (tipo === 'sticker') return 'ignorado_sticker';

    if (tipo === 'ptt' || tipo === 'audio') return 'audios';
    if (tipo === 'image' || mime.startsWith('image/')) {
        return 'imagens';
    }

    if (tipo === 'video' || mime.startsWith('video/')) {
        return 'videos';
    }

    if (tipo === 'document' || mime.startsWith('application/')) {
        return 'documentos';
    }

    if (mime.startsWith('audio/')) return 'audios';

    return 'outros';
}

function extensaoArquivo(media, mime) {
    const original = path.extname(
        path.basename(media.filename || '')
    );

    if (/^\.[a-zA-Z0-9]{1,10}$/.test(original)) {
        return original.toLowerCase();
    }

    return EXTENSOES[mime] || '.bin';
}

function sha256(buffer) {
    return crypto
        .createHash('sha256')
        .update(buffer)
        .digest('hex');
}

function arquivoValido(registro) {
    if (!registro.caminho) return false;

    const destino = path.resolve(DATA_DIR, registro.caminho);

    if (!destino.startsWith(path.resolve(DATA_DIR) + path.sep)) {
        return false;
    }

    if (!fs.existsSync(destino)) return false;

    const buffer = fs.readFileSync(destino);

    if (!buffer.length) return false;

    if (registro.sha256 && sha256(buffer) !== registro.sha256) {
        return false;
    }

    return true;
}

function pausa(ms) {
    return new Promise(resolve => setTimeout(resolve, ms));
}

const mensagensPorCanal = new Map();


async function buscarHistoricoDisponivel(canal) {
    let limite = 10000;
    const limiteMaximo = 1000000;

    while (true) {
        console.log(`Consultando histórico com limite ${limite}...`);

        const mensagens = await canal.fetchMessages({
            limit: limite
        });

        console.log(`Mensagens recuperadas: ${mensagens.length}`);

        if (mensagens.length < limite) {
            console.log('Fim do histórico disponibilizado pelo WhatsApp.');
            return mensagens;
        }

        if (limite >= limiteMaximo) {
            throw new Error(
                'Limite máximo atingido. Histórico pode estar incompleto.'
            );
        }

        limite = Math.min(limite * 2, limiteMaximo);
    }
}


async function coletarHistorico(canal, config) {
    const iniciada = new Date().toISOString();

    console.log('\n========================================');
    console.log('CANAL:', config.nome);
    console.log('ID:', config.id);
    console.log('Carregando histórico completo disponível...');

    const mensagens = await buscarHistoricoDisponivel(canal);

    const mapa = new Map();

    for (const m of mensagens) {
        const id = idMensagem(m);

        if (id) mapa.set(id, m);
    }

    const unicas = [...mapa.values()];

    const datas = unicas
        .filter(m => m.timestamp != null && Number.isFinite(Number(m.timestamp)))
        .map(m => new Date(Number(m.timestamp) * 1000).toISOString())
        .sort();

    const primeira = datas[0] || null;
    const ultima = datas.at(-1) || null;

    let quantidadeMidias = 0;
    let ptt = 0;
    let imagens = 0;
    let videos = 0;
    let documentos = 0;
    let ptv = 0;
    let stickers = 0;
    let outros = 0;

    db.exec('BEGIN IMMEDIATE');

    try {
        inserirCanal.run(config.id, canal.name || config.nome);

        for (const m of unicas) {
            const id = idMensagem(m);
            const timestamp = m.timestamp != null &&
                Number.isFinite(Number(m.timestamp))
                ? Number(m.timestamp)
                : null;

            const data = timestamp !== null
                ? new Date(timestamp * 1000).toISOString()
                : null;

            inserirMensagem.run(
                config.id,
                id,
                timestamp,
                data,
                m.type ?? null,
                m.body ?? '',
                m.hasMedia ? 1 : 0
            );

            if (!m.hasMedia) continue;

            quantidadeMidias++;

            const tipo = m.type || 'unknown';
            const mimetype = m.rawData?.mimetype || null;
            const nome = m.rawData?.filename || null;

            const categoria = classificar(tipo, mimetype);
            const anterior = consultarMidia.get(config.id, id);

            if (tipo === 'ptt' || tipo === 'audio') ptt++;
            else if (categoria === 'imagens') imagens++;
            else if (categoria === 'videos') videos++;
            else if (categoria === 'documentos') documentos++;
            else if (categoria === 'ignorado_ptv') ptv++;
            else if (categoria === 'ignorado_sticker') stickers++;
            else outros++;

            let status = 'pendente';

            if (categoria === 'ignorado_ptv') {
                status = anterior?.status === 'baixado'
                    ? 'baixado'
                    : 'ignorado_ptv';
            } else if (categoria === 'ignorado_sticker') {
                status = anterior?.status === 'baixado'
                    ? 'baixado'
                    : 'ignorado_sticker';
            } else if (
                anterior?.status === 'baixado' &&
                arquivoValido(anterior)
            ) {
                status = 'baixado';
            }

            inserirMidia.run(
                config.id,
                id,
                tipo,
                mimetype,
                nome,
                status,
                new Date().toISOString()
            );
        }

        atualizarCanal.run(
            new Date().toISOString(),
            primeira,
            ultima,
            unicas.length,
            config.id
        );

        inserirColeta.run(
            config.id,
            iniciada,
            new Date().toISOString(),
            mensagens.length,
            unicas.length,
            primeira,
            ultima,
            quantidadeMidias,
            null
        );

        db.exec('COMMIT');

    } catch (error) {
        db.exec('ROLLBACK');
        throw error;
    }

    mensagensPorCanal.set(config.id, unicas);

    console.log('Mensagens retornadas:', mensagens.length);
    console.log('Mensagens únicas:', unicas.length);
    console.log('Primeira publicação:', primeira);
    console.log('Última publicação:', ultima);
    console.log('Publicações com mídia:', quantidadeMidias);

    console.log('\nDistribuição:');
    console.log({
        audios: ptt,
        imagens,
        videos,
        documentos,
        ptv_ignorados: ptv,
        figurinhas_ignoradas: stickers,
        outros
    });
}


async function baixarMidiaNewsletter(mensagem) {
    const id = mensagem.id?._serialized;

    const mimetype = String(
        mensagem.rawData?.mimetype ||
        mensagem.mimetype ||
        ''
    );

    const mime = mimetype.split(';')[0].trim().toLowerCase();

    let tipoNewsletter;

    if (mime.startsWith('image/')) {
        tipoNewsletter = 'newsletter-image';
    } else if (mime.startsWith('video/')) {
        tipoNewsletter = 'newsletter-video';
    } else if (mime.startsWith('audio/')) {
        tipoNewsletter = 'newsletter-audio';
    } else {
        throw new Error(`MIME não suportado: ${mime}`);
    }

    if (!id) {
        throw new Error('Identificador da mensagem ausente.');
    }

    const pagina = mensagem.client?.pupPage;

    if (!pagina) {
        throw new Error('Página do WhatsApp não encontrada na mensagem.');
    }

    return await pagina.evaluate(
        async ({ id, tipoNewsletter }) => {
            const Msg = window.require('WAWebCollections').Msg;

            const msg = Msg.get(id) ||
                (await Msg.getMessagesById([id]))?.messages?.[0];

            if (!msg) {
                throw new Error('Mensagem não encontrada no WhatsApp Web.');
            }

            if (!msg.directPath || !msg.filehash) {
                throw new Error('DirectPath ou filehash ausente.');
            }

            if (
                msg.mediaData &&
                msg.mediaData.mediaStage !== 'RESOLVED'
            ) {
                await msg.downloadMedia({
                    downloadEvenIfExpensive: true,
                    rmrReason: 1
                });
            }

            const mockQpl = new Proxy({}, {
                get(target, prop, receiver) {
                    if (Reflect.has(target, prop)) {
                        return Reflect.get(target, prop, receiver);
                    }

                    return (...args) => receiver;
                }
            });

            const bytes = await window
                .require('WAWebDownloadManager')
                .downloadManager
                .downloadAndMaybeDecrypt({
                    directPath: msg.directPath,
                    encFilehash: msg.encFilehash,
                    filehash: msg.filehash,
                    mediaKey: msg.mediaKey,
                    mediaKeyTimestamp: msg.mediaKeyTimestamp,
                    type: tipoNewsletter,
                    mimetype: msg.mimetype,
                    signal: new AbortController().signal,
                    downloadQpl: mockQpl
                });

            let buffer;

            if (bytes instanceof ArrayBuffer) {
                buffer = bytes;
            } else if (ArrayBuffer.isView(bytes)) {
                buffer = bytes.buffer.slice(
                    bytes.byteOffset,
                    bytes.byteOffset + bytes.byteLength
                );
            } else {
                throw new Error('Formato de retorno inesperado.');
            }

            const data = await window.WWebJS.arrayBufferToBase64Async(
                buffer
            );

            return {
                data,
                mimetype: msg.mimetype,
                filename: msg.filename || null,
                filesize: msg.size,
                filehash: msg.filehash
            };
        },
        { id, tipoNewsletter }
    );
}

async function baixarMidia(config, mensagem) {
    const id = idMensagem(mensagem);

    const registro = consultarMidia.get(config.id, id);

    if (!registro) return 'erro';

    if (
        registro.status === 'baixado' &&
        arquivoValido(registro)
    ) {
        return 'ja_existia';
    }

    if (
        registro.status === 'ignorado_ptv' ||
        registro.status === 'ignorado_sticker'
    ) {
        return registro.status;
    }

    const tentativa = (registro.tentativas || 0) + 1;
    const agora = new Date().toISOString();

    atualizarStatus.run(
        'baixando',
        null,
        tentativa,
        agora,
        config.id,
        id
    );

    try {
        const media = await baixarMidiaNewsletter(mensagem);

        if (!media || !media.data) {
            throw new Error('Mídia indisponível para download.');
        }

        const mime = String(media.mimetype || '')
            .split(';')[0]
            .trim()
            .toLowerCase();

        const base64 = media.data.includes(',')
            ? media.data.split(',').pop()
            : media.data;

        const buffer = Buffer.from(base64, 'base64');

        if (!buffer.length) {
            throw new Error('Arquivo vazio.');
        }

        if (!media.filehash) {
            throw new Error('filehash ausente; arquivo não será salvo.');
        }

        const hashBase64 = crypto
            .createHash('sha256')
            .update(buffer)
            .digest('base64');

        if (hashBase64 !== media.filehash) {
            throw new Error('SHA-256 divergente do filehash do WhatsApp.');
        }

        if (
            media.filesize != null &&
            Number(media.filesize) > 0 &&
            Number(media.filesize) !== buffer.length
        ) {
            throw new Error('Tamanho recebido diferente do tamanho esperado.');
        }

        const categoria = classificar(mensagem.type, mime);
        const pasta = path.join(
            MEDIA_DIR,
            slug(config.nome),
            categoria
        );

        fs.mkdirSync(pasta, { recursive: true });

        const extensao = extensaoArquivo(media, mime);
        const nome = slug(id) + extensao;

        const destino = path.join(pasta, nome);
        const temporario = destino + '.part';

        const relativo = path.relative(DATA_DIR, destino);

        fs.writeFileSync(temporario, buffer);
        fs.renameSync(temporario, destino);

        const hash = sha256(buffer);

        atualizarMidia.run(
            media.mimetype,
            media.filename || null,
            relativo,
            buffer.length,
            hash,
            'baixado',
            null,
            new Date().toISOString(),
            tentativa,
            new Date().toISOString(),
            config.id,
            id
        );

        return 'baixado';

    } catch (error) {
        const indisponivel = error.message.includes('indisponível');

        atualizarStatus.run(
            indisponivel ? 'indisponivel' : 'erro',
            error.message,
            tentativa,
            new Date().toISOString(),
            config.id,
            id
        );

        console.error(`Falha ${config.nome} / ${id}: ${error.message}`);

        return indisponivel ? 'indisponivel' : 'erro';
    }
}

async function processarDownloads() {
    console.log('\n========================================');
    console.log('INICIANDO FILA DE DOWNLOADS');

    const tarefas = [];

    for (const config of canais) {
        const mensagens = mensagensPorCanal.get(config.id) || [];

        for (const m of mensagens) {
            if (!m.hasMedia) continue;

            const id = idMensagem(m);
            if (!id) continue;

            const tipo = m.type || 'unknown';
            const mime = m.rawData?.mimetype || null;
            const categoria = classificar(tipo, mime);

            const mimeBase = String(mime || '')
                .split(';')[0]
                .trim()
                .toLowerCase();

            if (
                (tipo === 'unknown' && mimeBase === 'image/webp') ||
                categoria === 'ignorado_ptv' ||
                categoria === 'ignorado_sticker'
            ) {
                continue;
            }

            const registro = consultarMidia.get(config.id, id);

            if (!registro) continue;

            if (registro.status === 'baixado' && arquivoValido(registro)) {
                continue;
            }

            tarefas.push({ config, mensagem: m });
        }
    }

    console.log('Arquivos pendentes:', tarefas.length);

    let baixados = 0;
    let existentes = 0;
    let erros = 0;
    let indisponiveis = 0;

    for (let i = 0; i < tarefas.length; i++) {
        const tarefa = tarefas[i];

        console.log(
            `\n[${i + 1}/${tarefas.length}] ` +
            `${tarefa.config.nome} | ${tarefa.mensagem.type}`
        );

        const resultado = await baixarMidia(
            tarefa.config,
            tarefa.mensagem
        );

        if (resultado === 'baixado') baixados++;
        else if (resultado === 'ja_existia') existentes++;
        else if (resultado === 'indisponivel') indisponiveis++;
        else if (resultado === 'erro') erros++;

        if ((i + 1) % 10 === 0 || i + 1 === tarefas.length) {
            console.log('\nPROGRESSO:', {
                processadas: i + 1,
                total: tarefas.length,
                baixados,
                existentes,
                indisponiveis,
                erros
            });
        }

        await pausa(PAUSA_DOWNLOAD);
    }

    console.log('\n========== RESUMO DOS DOWNLOADS ==========');
    console.log({
        tarefas: tarefas.length,
        baixados,
        ja_existiam: existentes,
        indisponiveis,
        erros
    });
}

async function main() {
    const client = new Client({
        authStrategy: new LocalAuth({
        clientId: 'canal-teste',
        dataPath: path.join(ROOT_DIR, '.wwebjs_auth')
    }),
        puppeteer: {
            headless: true
        }
    });

    client.on('qr', qr => {
        require('qrcode-terminal').generate(qr, { small: true });
    });

    client.on('auth_failure', error => {
        console.error('Falha de autenticação:', error);
    });

    try {
        await new Promise((resolve, reject) => {
            client.once('ready', resolve);
            client.once('auth_failure', reject);
            client.initialize().catch(reject);
        });

        console.log('WhatsApp conectado.');
        console.log('Banco:', DB_PATH);
        console.log('Canais configurados:', canais.length);

        for (const config of canais) {
            try {
                const canal = await client.getChatById(config.id);

                if (!canal || !canal.isChannel) {
                    throw new Error('Canal não encontrado.');
                }

                await coletarHistorico(canal, config);

            } catch (error) {
                console.error(
                    `Erro na coleta de ${config.nome}:`,
                    error.message
                );
            }
        }

        await processarDownloads();

    } finally {
        await client.destroy();
        db.close();
    }
}

main().catch(error => {
    console.error('ERRO FATAL:', error);
    try {
        db.close();
    } catch {}
    process.exitCode = 1;
});
