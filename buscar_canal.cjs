
const { Client, LocalAuth } = require('whatsapp-web.js');

const termo = process.argv.slice(2).join(' ').trim();

if (!termo) {
    console.error('Informe o nome do canal.');
    process.exit(1);
}

const client = new Client({
    authStrategy: new LocalAuth({
        clientId: 'canal-teste'
    }),
    puppeteer: {
        headless: true
    }
});

client.once('authenticated', () => {
    console.log('Sessão autenticada.');
});

client.once('ready', async () => {
    console.log('WhatsApp conectado.');
    console.log(`Pesquisando: ${termo}`);

    try {
        const canais = await client.searchChannels({
            searchText: termo,
            limit: 20
        });

        console.log(`\nCanais encontrados: ${canais.length}\n`);

        for (const [i, canal] of canais.entries()) {
            console.log({
                indice: i + 1,
                nome: canal.name,
                id: canal.id?._serialized ?? canal.id?.$1 ?? null,
                descricao: canal.description ?? null,
                tipo: canal.isChannel
            });
        }

    } catch (error) {
        console.error('Erro na pesquisa:', error);
        process.exitCode = 1;
    } finally {
        await client.destroy();
    }
});

client.initialize().catch(error => {
    console.error('Erro ao inicializar:', error);
    process.exitCode = 1;
});
