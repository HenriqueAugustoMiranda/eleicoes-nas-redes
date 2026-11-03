const { Client, LocalAuth } = require('whatsapp-web.js');
const { execFileSync } = require('child_process');
const fs = require('fs');
const path = require('path');
const crypto = require('crypto');

const ROOT_DIR = path.resolve(__dirname, '../..');
const BASE = process.env.WA_DATA_DIR || ROOT_DIR;
const DB = path.join(BASE, 'db', 'whatsapp.sqlite');
const CONFIG = path.join(ROOT_DIR, 'config', 'canais.json');

const canais = JSON.parse(
    fs.readFileSync(CONFIG, 'utf8')
);

function consultar(sql) {
    const saida = execFileSync(
        'sqlite3',
        ['-json', DB, sql],
        { encoding: 'utf8' }
    );

    return JSON.parse(saida.trim() || '[]');
}

function executar(sql) {
    execFileSync('sqlite3', [DB, sql], {
        encoding: 'utf8'
    });
}

function q(valor) {
    if (valor === null || valor === undefined) return 'NULL';
    return "'" + String(valor).replace(/'/g, "''") + "'";
}

function slug(texto) {
    return String(texto)
        .normalize('NFD')
        .replace(/[\u0300-\u036f]/g, '')
        .toLowerCase()
        .replace(/[^a-z0-9_-]+/g, '_')
        .replace(/^_+|_+$/g, '');
}

function sha256(buffer, formato = 'hex') {
    return crypto.createHash('sha256')
        .update(buffer)
        .digest(formato);
}

async function hashArquivo(arquivo) {
    return new Promise((resolve, reject) => {
        const hash = crypto.createHash('sha256');
        const stream = fs.createReadStream(arquivo);

        stream.on('data', bloco => hash.update(bloco));
        stream.on('error', reject);
        stream.on('end', () => resolve(hash.digest('hex')));
    });
}

async function arquivoValido(registro) {
    if (!registro.caminho || !registro.sha256) return false;

    const arquivo = path.resolve(BASE, registro.caminho);

    if (!arquivo.startsWith(BASE + path.sep)) return false;
    if (!fs.existsSync(arquivo)) return false;

    const stat = fs.statSync(arquivo);

    if (
        registro.tamanho_bytes &&
        stat.size !== Number(registro.tamanho_bytes)
    ) {
        return false;
    }

    return await hashArquivo(arquivo) === registro.sha256;
}

function atualizarErro(registro, tentativa, erro) {
    const agora = new Date().toISOString();

    executar(`
        UPDATE midias SET
            status = 'erro',
            erro = ${q(erro)},
            tentativas = ${tentativa},
            atualizado_em = ${q(agora)}
        WHERE canal_id = ${q(registro.canal_id)}
          AND mensagem_id = ${q(registro.mensagem_id)};
    `);
}

async function baixarPTV(client, mensagem) {
    const id = mensagem.id?._serialized;

    return await client.pupPage.evaluate(async id => {
        const Msg = window.require('WAWebCollections').Msg;

        const msg = Msg.get(id) ||
            (await Msg.getMessagesById([id]))?.messages?.[0];

        if (!msg) {
            throw new Error('Mensagem não encontrada.');
        }

        if (msg.type !== 'ptv') {
            throw new Error(`Tipo inesperado: ${msg.type}`);
        }

        if (!msg.directPath || !msg.filehash) {
            throw new Error('DirectPath ou filehash ausente.');
        }

        if (
            msg.mediaData &&
            msg.mediaData.mediaStage !== 'RESOLVED'
        ) {
            try {
                await msg.downloadMedia({
                    downloadEvenIfExpensive: true,
                    rmrReason: 1
                });
            } catch {}
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
                type: 'newsletter-ptv',
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

        return {
            data: await window.WWebJS.arrayBufferToBase64Async(buffer),
            mimetype: msg.mimetype,
            filesize: msg.size,
            filehash: msg.filehash,
            filename: msg.filename || null
        };
    }, id);
}

function registrarDownload(registro, caminho, buffer, media, tentativa) {
    const agora = new Date().toISOString();
    const relativo = path.relative(BASE, caminho);

    executar(`
        UPDATE midias SET
            mimetype = ${q(media.mimetype)},
            nome_original = COALESCE(
                ${q(media.filename)},
                nome_original
            ),
            caminho = ${q(relativo)},
            tamanho_bytes = ${buffer.length},
            sha256 = ${q(sha256(buffer))},
            status = 'baixado',
            erro = NULL,
            baixado_em = ${q(agora)},
            tentativas = ${tentativa},
            atualizado_em = ${q(agora)}
        WHERE canal_id = ${q(registro.canal_id)}
          AND mensagem_id = ${q(registro.mensagem_id)};
    `);
}

async function main() {
    if (!fs.existsSync(DB)) {
        throw new Error('Banco de dados não encontrado.');
    }

    const registros = consultar(`
        SELECT *
        FROM midias
        WHERE tipo = 'ptv'
        ORDER BY canal_id, mensagem_id;
    `);

    console.log('PTVs encontrados:', registros.length);

    if (!registros.length) return;

    const client = new Client({
        authStrategy: new LocalAuth({
        clientId: 'canal-teste',
        dataPath: path.join(ROOT_DIR, '.wwebjs_auth')
    }),
        puppeteer: { headless: true }
    });

    const resultado = {
        total: registros.length,
        ja_validos: 0,
        baixados: 0,
        erros: 0
    };

    client.once('ready', async () => {
        try {
            console.log('WhatsApp conectado.');

            for (const config of canais) {
                const tarefas = registros.filter(
                    r => r.canal_id === config.id
                );

                if (!tarefas.length) continue;

                console.log('\n================================');
                console.log('CANAL:', config.nome);
                console.log('PTVs:', tarefas.length);

                const canal = await client.getChatById(config.id);
                const mensagens = await canal.fetchMessages({
                    limit: 10000
                });

                const mapa = new Map(
                    mensagens.map(m => [m.id?._serialized, m])
                );

                for (const registro of tarefas) {
                    const id = registro.mensagem_id;

                    console.log('\nPTV:', id);

                    if (
                        registro.status === 'baixado' &&
                        await arquivoValido(registro)
                    ) {
                        console.log('Arquivo existente e válido.');
                        resultado.ja_validos++;
                        continue;
                    }

                    const mensagem = mapa.get(id);

                    const tentativa =
                        Number(registro.tentativas || 0) + 1;

                    if (!mensagem) {
                        atualizarErro(
                            registro,
                            tentativa,
                            'Mensagem não encontrada no histórico.'
                        );
                        console.log('Mensagem não encontrada.');
                        resultado.erros++;
                        continue;
                    }

                    try {
                        executar(`
                            UPDATE midias SET
                                status = 'baixando',
                                erro = NULL,
                                tentativas = ${tentativa},
                                atualizado_em = ${q(new Date().toISOString())}
                            WHERE canal_id = ${q(registro.canal_id)}
                              AND mensagem_id = ${q(id)};
                        `);

                        const media = await baixarPTV(client, mensagem);
                        const buffer = Buffer.from(media.data, 'base64');

                        if (!buffer.length) {
                            throw new Error('Arquivo vazio.');
                        }

                        if (!media.filehash) {
                            throw new Error('Filehash ausente.');
                        }

                        if (sha256(buffer, 'base64') !== media.filehash) {
                            throw new Error('SHA-256 divergente.');
                        }

                        if (
                            media.filesize != null &&
                            Number(media.filesize) > 0 &&
                            Number(media.filesize) !== buffer.length
                        ) {
                            throw new Error('Tamanho recebido divergente.');
                        }

                        const pasta = path.join(
                            BASE,
                            'midias',
                            slug(config.nome),
                            'videos_redondos'
                        );

                        fs.mkdirSync(pasta, { recursive: true });

                        const nome = slug(id) + '.mp4';
                        const destino = path.join(pasta, nome);
                        const temporario =
                            destino + `.${process.pid}.part`;

                        fs.writeFileSync(temporario, buffer);
                        fs.renameSync(temporario, destino);

                        registrarDownload(
                            registro,
                            destino,
                            buffer,
                            media,
                            tentativa
                        );

                        console.log('BAIXADO E VALIDADO:', buffer.length, 'bytes');
                        resultado.baixados++;

                    } catch (error) {
                        atualizarErro(
                            registro,
                            tentativa,
                            error.message || String(error)
                        );

                        console.error('ERRO:', error.message);
                        resultado.erros++;
                    }
                }
            }

        } catch (error) {
            console.error('ERRO GERAL:', error);
        } finally {
            console.log('\n========== RESULTADO PTV ==========');
            console.log(resultado);
            await client.destroy();
        }
    });

    client.initialize().catch(async error => {
        console.error(error);
        await client.destroy();
    });
}

main().catch(console.error);
