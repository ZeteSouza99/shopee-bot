// ─── Shopee Afiliados - Frontend Application ───
// Sanitizado contra XSS, com autenticação e separação de concerns.

"use strict";

// ─── Segurança: Escape de HTML ───

function esc(str) {
    if (str === null || str === undefined) return '';
    const div = document.createElement('div');
    div.textContent = String(str);
    return div.innerHTML;
}

function safeUrl(url) {
    if (!url) return '';
    try {
        const u = new URL(url);
        if (u.protocol === 'http:' || u.protocol === 'https:') return esc(url);
        return '';
    } catch { return ''; }
}

// ─── Global state ───
let lastProductNodes = [];
let lastRankNodes    = [];
let lastLojaNodes    = [];
let lastOfertaNodes  = [];
let _postTexts       = [];

// ─── Tab navigation ───
document.querySelectorAll('.tab').forEach(tab => {
    tab.addEventListener('click', () => {
        document.querySelectorAll('.tab').forEach(t => t.classList.remove('active'));
        document.querySelectorAll('.panel').forEach(p => p.classList.remove('active'));
        tab.classList.add('active');
        document.getElementById('panel-' + tab.dataset.tab).classList.add('active');
        if (tab.dataset.tab === 'favoritas') carregarFavoritas();
        if (tab.dataset.tab === 'configuracoes') carregarCredenciais();
    });
});

// ─── Helpers ───
function showLoading(id) {
    document.getElementById(id).innerHTML = '<div class="loading"><div class="spinner"></div><p>Consultando API...</p></div>';
}
function showError(id, error) {
    let html = '<div class="error-msg"><strong>Erro</strong>';
    if (typeof error === 'object' && error.errors) {
        error.errors.forEach(e => {
            html += `<p>Código ${esc(e.extensions?.code||'?')}: ${esc(e.message)}</p>`;
        });
    } else if (typeof error === 'string') html += `<p>${esc(error)}</p>`;
    else html += `<p>${esc(JSON.stringify(error))}</p>`;
    html += '</div>';
    document.getElementById(id).innerHTML = html;
}
function jsonToggle(id) { document.getElementById(id).classList.toggle('show'); }
function copyLink(text) { navigator.clipboard.writeText(text); }
function formatComm(rate) {
    try { return (parseFloat(rate) * 100).toFixed(1) + '%'; }
    catch { return rate || '0%'; }
}
function formatPrice(product) {
    const price = parseFloat(product.priceMin);
    const discount = parseFloat(product.priceDiscountRate);
    if (isNaN(price)) return '<span>Preço indisponível</span>';

    if (!isNaN(discount) && discount > 0) {
        const original = price / (1 - discount / 100);
        return `<span style="text-decoration:line-through;color:var(--text-secondary)">R$ ${esc(original.toFixed(2))}</span>
            <span style="color:var(--success);font-weight:700">R$ ${esc(price.toFixed(2))}</span>
            <span style="background:var(--danger);color:white;padding:1px 8px;border-radius:10px;font-size:11px;font-weight:600">-${Math.round(discount)}%</span>`;
    }
    return `<span style="font-weight:600">R$ ${esc(price.toFixed(2))}</span>`;
}
function formatDate(ts) { return ts ? new Date(ts * 1000).toLocaleDateString('pt-BR') : '-'; }
function formatDateTime(ts) { return ts ? new Date(ts * 1000).toLocaleString('pt-BR') : '-'; }
function shopTypeBadge(type) {
    const m = {1:['Mall','mall'],2:['Star','star'],4:['Star+','starplus']};
    const i = m[type]||['?',''];
    return `<span class="shop-type-badge ${i[1]}">${i[0]}</span>`;
}
function buildJsonBlock(data, id) {
    return `<div class="json-toggle"><button class="btn btn-secondary btn-sm" onclick="jsonToggle('${esc(id)}')">Ver/Ocultar JSON</button><pre class="json-content" id="${esc(id)}">${esc(JSON.stringify(data, null, 2))}</pre></div>`;
}
async function apiCall(url, body) {
    const r = await fetch(url, { method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify(body) });
    if (r.status === 401) {
        window.location.href = '/login';
        throw new Error('Sessão expirada');
    }
    const d = await r.json();
    if (d.error) throw d.error;
    if (d.errors && !d.data) throw d;
    return d;
}

// ─── Produtos ───
async function buscarProdutos() {
    showLoading('result-produtos');
    try {
        const body = {
            keyword: document.getElementById('prod-keyword').value,
            listType: parseInt(document.getElementById('prod-listType').value),
            sortType: parseInt(document.getElementById('prod-sortType').value),
            limit: parseInt(document.getElementById('prod-limit').value),
            page: parseInt(document.getElementById('prod-page').value),
        };
        if (document.getElementById('prod-isAMS').checked) body.isAMSOffer = true;
        if (document.getElementById('prod-isKey').checked) body.isKeySeller = true;
        const data = await apiCall('/api/produtos', body);
        renderProdutos(data, 'result-produtos');
    } catch (e) { showError('result-produtos', e); }
}

function testeRapido() {
    document.getElementById('prod-keyword').value = 'espelho';
    document.getElementById('prod-limit').value = '5';
    buscarProdutos();
}

async function verProdutosDaLoja(shopId, shopName) {
    document.querySelectorAll('.tab').forEach(t => t.classList.remove('active'));
    document.querySelectorAll('.panel').forEach(p => p.classList.remove('active'));
    document.querySelector('[data-tab="produtos"]').classList.add('active');
    document.getElementById('panel-produtos').classList.add('active');
    showLoading('result-produtos');
    try {
        const data = await apiCall('/api/produtos', { shopId, sortType:5, listType:1, page:1, limit:20 });
        const nodes = data.data?.productOfferV2?.nodes || [];
        let header = `<div style="display:flex;align-items:center;gap:12px;margin-bottom:16px">
            <button class="btn btn-secondary btn-sm" onclick="voltarParaLojas()">&#8592; Voltar</button>
            <h3 style="margin:0">Produtos de <span style="color:var(--primary)">${esc(shopName)}</span></h3>
            <span class="results-count">${nodes.length} produto(s)</span></div>`;
        if (!nodes.length) { document.getElementById('result-produtos').innerHTML = header + '<div class="empty-state">Nenhum produto encontrado nesta loja.</div>'; return; }
        renderProdutos(data, 'result-produtos');
        document.getElementById('result-produtos').insertAdjacentHTML('afterbegin', header);
    } catch (e) { showError('result-produtos', e); }
}

function voltarParaLojas() {
    document.querySelectorAll('.tab').forEach(t => t.classList.remove('active'));
    document.querySelectorAll('.panel').forEach(p => p.classList.remove('active'));
    document.querySelector('[data-tab="lojas"]').classList.add('active');
    document.getElementById('panel-lojas').classList.add('active');
}

function renderProdutos(data, containerId) {
    const nodes = data.data?.productOfferV2?.nodes || [];
    const pageInfo = data.data?.productOfferV2?.pageInfo || {};
    lastProductNodes = nodes;

    if (!nodes.length) { document.getElementById(containerId).innerHTML = '<div class="empty-state">Nenhum produto encontrado.</div>'; return; }

    let html = `<div class="results-header">
        <h3>Produtos Encontrados</h3>
        <div style="display:flex;gap:8px;align-items:center;flex-wrap:wrap">
            <span class="results-count">${nodes.length} resultado(s) | Página ${pageInfo.page || 1}</span>
            <button class="btn btn-success btn-sm" onclick="exportarCSV()">Exportar CSV</button>
            <button class="btn btn-secondary btn-sm" onclick="salvarHistorico()">Salvar no Histórico</button>
        </div></div>`;

    nodes.forEach((p, idx) => {
        const imgUrl = safeUrl(p.imageUrl);
        const img = imgUrl ? `<img src="${imgUrl}" alt="" loading="lazy">` : '<img src="" alt="">';
        const rating = p.ratingStar ? `<span>&#9733; ${esc(p.ratingStar)}</span>` : '';
        html += `<div class="product-card">
            ${img}
            <div class="product-info">
                <h4>${esc(p.productName) || 'Sem nome'}</h4>
                <div class="product-meta">
                    <span><span class="commission-badge">${formatComm(p.commissionRate)}</span></span>
                    <span>${formatPrice(p)}</span>
                    ${rating}
                    <span>${esc(p.sales) || 0} vendas</span>
                    <span>Est. R$ ${esc(p.commission) || '?'}</span>
                    ${p.shopName ? `<span>${shopTypeBadge(p.shopType)} ${esc(p.shopName)}</span>` : ''}
                </div>
                ${p.sellerCommissionRate ? `<div class="product-meta" style="margin-top:4px"><span>Vendedor: ${formatComm(p.sellerCommissionRate)}</span><span>Shopee: ${formatComm(p.shopeeCommissionRate)}</span></div>` : ''}
                ${p.periodEndTime ? `<div class="product-meta" style="margin-top:4px"><span style="font-size:11px;color:${p.periodEndTime*1000 < Date.now()+3*86400000 ? 'var(--danger)' : 'var(--text-secondary)'}">Oferta: ${formatDate(p.periodStartTime)} a ${formatDate(p.periodEndTime)}${p.periodEndTime*1000 < Date.now()+3*86400000 ? ' (expirando!)' : ''}</span></div>` : ''}
                <div class="affiliate-link-area" id="aff-link-${idx}"><span class="generating-link">Gerando link de afiliado...</span></div>
                <div class="social-btns">
                    <button class="btn btn-secondary btn-sm" onclick="gerarPost(${idx})">Gerar Post</button>
                    <button class="btn btn-primary btn-sm" onclick="abrirPublishModal('produto',${idx})">&#128227; Compartilhar</button>
                </div>
                <div id="social-post-${idx}" style="display:none"></div>
            </div></div>`;
    });

    if (pageInfo.hasNextPage) {
        html += `<div class="pagination">
            <button onclick="document.getElementById('prod-page').value=${(pageInfo.page||1)-1};buscarProdutos()" ${(pageInfo.page||1)<=1?'disabled':''}>Anterior</button>
            <span class="page-info">Página ${pageInfo.page || 1}</span>
            <button onclick="document.getElementById('prod-page').value=${(pageInfo.page||1)+1};buscarProdutos()">Próxima</button></div>`;
    }
    html += buildJsonBlock(data, 'json-produtos');
    document.getElementById(containerId).innerHTML = html;
    gerarLinksAfiliado(nodes);
}

async function gerarLinksAfiliado(nodes) {
    const urls = nodes.map(p => p.offerLink || p.productLink).filter(Boolean);
    if (!urls.length) return;
    try {
        const r = await fetch('/api/short-links-lote', { method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({urls}) });
        if (r.status === 401) { window.location.href = '/login'; return; }
        const data = await r.json();
        const sl = data.shortLinks || {};
        nodes.forEach((p, idx) => {
            const el = document.getElementById(`aff-link-${idx}`);
            if (!el) return;
            const orig = p.offerLink || p.productLink;
            const short = sl[orig];
            if (short) {
                p._shortLink = short;
                const safeShort = safeUrl(short);
                el.innerHTML = `<a class="product-link affiliate-short" href="${safeShort}" target="_blank">${esc(short)}</a><button class="copy-btn" onclick="copyLink('${esc(short)}')">Copiar</button>`;
            } else if (orig) {
                const safeOrig = safeUrl(orig);
                el.innerHTML = `<a class="product-link" href="${safeOrig}" target="_blank">${esc(orig)}</a><button class="copy-btn" onclick="copyLink('${esc(orig)}')">Copiar</button><span class="link-fallback-note">link original</span>`;
            } else el.innerHTML = '<span class="link-fallback-note">Nenhum link disponível</span>';
        });
    } catch {
        nodes.forEach((p, idx) => {
            const el = document.getElementById(`aff-link-${idx}`);
            if (!el) return;
            const url = p.offerLink || p.productLink;
            if (url) {
                const safeU = safeUrl(url);
                el.innerHTML = `<a class="product-link" href="${safeU}" target="_blank">${esc(url)}</a><button class="copy-btn" onclick="copyLink('${esc(url)}')">Copiar</button>`;
            }
        });
    }
}

// ─── Gerador de Conteúdo ───
function gerarPost(idx) {
    const p = lastProductNodes[idx];
    if (!p) return;
    const el = document.getElementById(`social-post-${idx}`);
    const link = p._shortLink || p.offerLink || p.productLink || '';
    const comissao = formatComm(p.commissionRate);

    const price = parseFloat(p.priceMin);
    const discount = parseFloat(p.priceDiscountRate);
    let precoTexto = '';
    if (!isNaN(price)) {
        if (!isNaN(discount) && discount > 0) {
            const original = price / (1 - discount / 100);
            precoTexto = `De R$ ${original.toFixed(2)} por R$ ${price.toFixed(2)} (-${Math.round(discount)}%)`;
        } else {
            precoTexto = `R$ ${price.toFixed(2)}`;
        }
    }

    const postText = `${p.productName || 'Produto'}\n\n${precoTexto}\nComissão: ${comissao}\n${p.sales ? p.sales + ' vendidos' : ''}\n\nCompre aqui: ${link}`;

    const postIdx = _postTexts.length;
    _postTexts.push(postText);

    el.style.display = 'block';
    el.innerHTML = `<div class="social-post-box">${esc(postText)}</div>
        <div class="social-btns" style="margin-top:6px">
            <button class="btn btn-secondary btn-sm" onclick="copyLink(_postTexts[${postIdx}])">Copiar Texto</button>
            ${p.imageUrl ? `<button class="btn btn-secondary btn-sm" onclick="copyLink('${esc(p.imageUrl)}')">Copiar URL Imagem</button>` : ''}
        </div>`;
}

// ─── Export CSV ───
async function exportarCSV() {
    if (!lastProductNodes.length) return;
    const produtos = lastProductNodes.map(p => ({...p, shortLink: p._shortLink || ''}));
    try {
        const r = await fetch('/api/export-csv', { method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({produtos}) });
        if (r.status === 401) { window.location.href = '/login'; return; }
        const blob = await r.blob();
        const url = URL.createObjectURL(blob);
        const a = document.createElement('a'); a.href = url; a.download = 'produtos_shopee.csv'; a.click();
        URL.revokeObjectURL(url);
    } catch (e) { alert('Erro ao exportar: ' + e); }
}

// ─── Salvar Histórico ───
async function salvarHistorico() {
    if (!lastProductNodes.length) return;
    try {
        await fetch('/api/historico', { method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({produtos:lastProductNodes}) });
        alert('Snapshot salvo no histórico!');
    } catch (e) { alert('Erro ao salvar: ' + e); }
}

// ─── Lojas ───
async function buscarLojas() {
    showLoading('result-lojas');
    try {
        const shopType = Array.from(document.getElementById('loja-shopType').selectedOptions).map(o => parseInt(o.value));
        const minComm = document.getElementById('loja-minComm').value;
        const body = {
            keyword: document.getElementById('loja-keyword').value,
            sortType: parseInt(document.getElementById('loja-sortType').value),
            limit: parseInt(document.getElementById('loja-limit').value),
            page: parseInt(document.getElementById('loja-page').value),
        };
        if (shopType.length) body.shopType = shopType;
        if (document.getElementById('loja-isKey').checked) body.isKeySeller = true;
        if (minComm) body.sellerCommCoveRatio = (parseFloat(minComm) / 100).toFixed(2);
        const data = await apiCall('/api/ofertas-loja', body);
        renderLojas(data);
    } catch (e) { showError('result-lojas', e); }
}

function renderLojas(data) {
    const nodes = data.data?.shopOfferV2?.nodes || [];
    const pageInfo = data.data?.shopOfferV2?.pageInfo || {};
    if (!nodes.length) { document.getElementById('result-lojas').innerHTML = '<div class="empty-state">Nenhuma loja encontrada.</div>'; return; }
    lastLojaNodes = nodes;

    let html = `<div class="results-header"><h3>Lojas Encontradas</h3><span class="results-count">${nodes.length} resultado(s)</span></div>`;
    nodes.forEach((s, idx) => {
        const imgUrl = safeUrl(s.imageUrl);
        const img = imgUrl ? `<img src="${imgUrl}" alt="" loading="lazy">` : '<img src="" alt="">';
        const escapedName = esc(s.shopName || 'Sem nome');
        const shopData = JSON.stringify({shopId:s.shopId,shopName:s.shopName,commissionRate:s.commissionRate,shopType:s.shopType,ratingStar:s.ratingStar,remainingBudget:s.remainingBudget,imageUrl:s.imageUrl,offerLink:s.offerLink}).replace(/'/g,'&#39;');
        const safeLink = safeUrl(s.offerLink);
        html += `<div class="shop-card">${img}<div class="product-info">
            <h4>${shopTypeBadge(s.shopType)} ${escapedName}</h4>
            <div class="product-meta">
                <span><span class="commission-badge">${formatComm(s.commissionRate)}</span></span>
                <span>Nota: ${esc(s.ratingStar) || '?'}</span>
                <span>Budget: ${esc(s.remainingBudget) || '?'}</span>
                <span>${formatDate(s.periodStartTime)} a ${formatDate(s.periodEndTime)}</span>
            </div>
            <div style="margin-top:10px;display:flex;align-items:center;gap:8px;flex-wrap:wrap">
                ${safeLink ? `<a class="product-link" href="${safeLink}" target="_blank" style="margin:0">${esc(s.offerLink)}</a><button class="copy-btn" onclick="copyLink('${esc(s.offerLink)}')">Copiar</button>` : ''}
                ${s.shopId ? `<button class="btn btn-primary btn-sm" onclick="verProdutosDaLoja(${parseInt(s.shopId)},'${escapedName.replace(/'/g,"\\'")}')">Ver Produtos</button>` : ''}
                ${s.shopId ? `<button class="btn btn-success btn-sm" onclick='favoritarLoja(${shopData})'>Favoritar</button>` : ''}
                <button class="btn btn-primary btn-sm" onclick="abrirPublishModal('loja',${idx})">&#128227; Compartilhar</button>
            </div></div></div>`;
    });
    if (pageInfo.hasNextPage) {
        html += `<div class="pagination">
            <button onclick="document.getElementById('loja-page').value=${(pageInfo.page||1)-1};buscarLojas()" ${(pageInfo.page||1)<=1?'disabled':''}>Anterior</button>
            <span class="page-info">Página ${pageInfo.page || 1}</span>
            <button onclick="document.getElementById('loja-page').value=${(pageInfo.page||1)+1};buscarLojas()">Próxima</button></div>`;
    }
    html += buildJsonBlock(data, 'json-lojas');
    document.getElementById('result-lojas').innerHTML = html;
}

// ─── Favoritas ───
async function favoritarLoja(data) {
    try {
        const r = await fetch('/api/favoritas', { method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify(data) });
        if (r.status === 401) { window.location.href = '/login'; return; }
        const res = await r.json();
        alert(res.added ? 'Loja favoritada!' : 'Loja já está nos favoritos.');
    } catch (e) { alert('Erro: ' + e); }
}

async function carregarFavoritas() {
    showLoading('result-favoritas');
    try {
        const r = await fetch('/api/favoritas');
        if (r.status === 401) { window.location.href = '/login'; return; }
        const favs = await r.json();
        if (!favs.length) { document.getElementById('result-favoritas').innerHTML = '<div class="empty-state">Nenhuma loja favorita. Busque lojas e clique em "Favoritar".</div>'; return; }

        let html = `<div class="results-header"><h3>Suas Lojas Favoritas</h3><span class="results-count">${favs.length} loja(s)</span></div>`;
        favs.forEach(f => {
            const budget = parseFloat(f.remainingBudget) || 0;
            let barClass = '';
            if (budget < 100) barClass = 'budget-danger';
            else if (budget < 500) barClass = 'budget-warning';
            const escapedName = esc(f.shopName || '?');

            html += `<div class="fav-card">
                <div class="fav-info">
                    <h4>${shopTypeBadge(f.shopType)} ${escapedName}</h4>
                    <div class="product-meta">
                        <span><span class="commission-badge">${formatComm(f.commissionRate)}</span></span>
                        <span>Nota: ${esc(f.ratingStar) || '?'}</span>
                        <span>Budget: R$ ${esc(f.remainingBudget) || '?'}</span>
                    </div>
                    <div class="budget-bar"><div class="budget-bar-fill ${barClass}" style="width:${Math.min(budget/10, 100)}%"></div></div>
                    ${f.lastUpdated ? `<div class="fav-updated">Atualizado: ${formatDateTime(f.lastUpdated)}</div>` : ''}
                </div>
                <div class="fav-actions">
                    <button class="btn btn-primary btn-sm" onclick="verProdutosDaLoja(${parseInt(f.shopId)},'${escapedName.replace(/'/g,"\\'")}')">Ver Produtos</button>
                    <button class="btn btn-danger btn-sm" onclick="removerFavorita(${parseInt(f.shopId)})">Remover</button>
                </div></div>`;
        });
        document.getElementById('result-favoritas').innerHTML = html;
    } catch (e) { showError('result-favoritas', e); }
}

async function removerFavorita(shopId) {
    await fetch(`/api/favoritas/${shopId}`, { method:'DELETE' });
    carregarFavoritas();
}

async function atualizarFavoritas() {
    showLoading('result-favoritas');
    try {
        const r = await fetch('/api/favoritas/atualizar', { method:'POST', headers:{'Content-Type':'application/json'}, body:'{}' });
        if (r.status === 401) { window.location.href = '/login'; return; }
        const res = await r.json();
        alert(`${res.updated} loja(s) atualizada(s).`);
        carregarFavoritas();
    } catch (e) { showError('result-favoritas', e); }
}

// ─── Ofertas ───
async function buscarOfertas() {
    showLoading('result-ofertas');
    try {
        const body = {
            keyword: document.getElementById('oferta-keyword').value,
            sortType: parseInt(document.getElementById('oferta-sortType').value),
            limit: parseInt(document.getElementById('oferta-limit').value),
            page: parseInt(document.getElementById('oferta-page').value),
        };
        const data = await apiCall('/api/ofertas-shopee', body);
        renderOfertas(data);
    } catch (e) { showError('result-ofertas', e); }
}

function renderOfertas(data) {
    const nodes = data.data?.shopeeOfferV2?.nodes || [];
    const pageInfo = data.data?.shopeeOfferV2?.pageInfo || {};
    if (!nodes.length) { document.getElementById('result-ofertas').innerHTML = '<div class="empty-state">Nenhuma oferta encontrada.</div>'; return; }
    lastOfertaNodes = nodes;
    let html = `<div class="results-header"><h3>Ofertas Shopee</h3><span class="results-count">${nodes.length} resultado(s)</span></div>`;
    nodes.forEach((o, idx) => {
        const tipo = o.offerType === 1 ? 'Coleção' : 'Categoria';
        const safeLink = safeUrl(o.offerLink);
        html += `<div class="offer-card"><h4>${esc(o.offerName) || 'Sem nome'}</h4>
            <div class="product-meta">
                <span><span class="commission-badge">${formatComm(o.commissionRate)}</span></span>
                <span>Tipo: ${tipo}</span>
                <span>${formatDate(o.periodStartTime)} a ${formatDate(o.periodEndTime)}</span>
            </div>
            ${safeLink ? `<a class="product-link" href="${safeLink}" target="_blank">${esc(o.offerLink)}</a><button class="copy-btn" onclick="copyLink('${esc(o.offerLink)}')">Copiar</button>` : ''}
            <div class="social-btns" style="margin-top:8px">
                <button class="btn btn-primary btn-sm" onclick="abrirPublishModal('oferta',${idx})">&#128227; Compartilhar</button>
            </div>
        </div>`;
    });
    if (pageInfo.hasNextPage) {
        html += `<div class="pagination">
            <button onclick="document.getElementById('oferta-page').value=${(pageInfo.page||1)-1};buscarOfertas()" ${(pageInfo.page||1)<=1?'disabled':''}>Anterior</button>
            <span class="page-info">Página ${pageInfo.page || 1}</span>
            <button onclick="document.getElementById('oferta-page').value=${(pageInfo.page||1)+1};buscarOfertas()">Próxima</button></div>`;
    }
    html += buildJsonBlock(data, 'json-ofertas');
    document.getElementById('result-ofertas').innerHTML = html;
}

// ─── Short Link ───
async function gerarShortLink() {
    const url = document.getElementById('sl-url').value;
    if (!url) { showError('result-shortlink', 'Informe a URL da Shopee.'); return; }
    showLoading('result-shortlink');
    try {
        const body = { originUrl: url };
        const subids = document.getElementById('sl-subids').value;
        if (subids) body.subIds = subids;
        const data = await apiCall('/api/short-link', body);
        const short = data.data?.generateShortLink?.shortLink;
        if (short) {
            const safeShort = safeUrl(short);
            document.getElementById('result-shortlink').innerHTML = `<div class="short-link-result">
                <a href="${safeShort}" target="_blank">${esc(short)}</a>
                <p>Link de afiliado gerado com sucesso!</p>
                <button class="btn btn-primary" style="margin-top:12px" onclick="copyLink('${esc(short)}')">Copiar Link</button>
            </div>${buildJsonBlock(data,'json-shortlink')}`;
        } else showError('result-shortlink', data);
    } catch (e) { showError('result-shortlink', e); }
}

// ─── Ranking ───
async function buscarRanking() {
    showLoading('result-ranking');
    try {
        const body = { keyword: document.getElementById('rank-keyword').value, limit: parseInt(document.getElementById('rank-limit').value) };
        const r = await fetch('/api/ranking', { method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify(body) });
        if (r.status === 401) { window.location.href = '/login'; return; }
        const data = await r.json();
        renderRanking(data);
    } catch (e) { showError('result-ranking', e); }
}

function renderRanking(data) {
    const cats = [
        { key:'recomendados',   title:'Recomendados',    color:'var(--info)' },
        { key:'maiorComissao',  title:'Maior Comissão',  color:'var(--primary)' },
        { key:'maisVendidos',   title:'Mais Vendidos',   color:'var(--warning)' },
        { key:'topPerformance', title:'Top Performance', color:'var(--success)' },
    ];

    let allRankProducts = [];
    lastRankNodes = [];

    let html = '<div class="ranking-grid">';
    cats.forEach(cat => {
        const items = data[cat.key] || [];
        html += `<div class="ranking-column"><h4 style="color:${cat.color}">${cat.title} (${items.length})</h4>`;
        if (!items.length) html += '<div class="empty-state">Sem resultados</div>';
        items.forEach((p, i) => {
            const globalIdx = allRankProducts.length;
            allRankProducts.push(p);
            lastRankNodes.push(p);
            const imgUrl = safeUrl(p.imageUrl);
            const img = imgUrl ? `<img src="${imgUrl}" alt="" loading="lazy">` : '<img src="" alt="">';
            html += `<div class="ranking-item">
                ${img}
                <div>
                    <div class="ri-name">${i+1}. ${esc((p.productName||'?').substring(0,60))}</div>
                    <div class="ri-meta">
                        <span><span class="commission-badge">${formatComm(p.commissionRate)}</span></span>
                        <span>${formatPrice(p)}</span>
                        <span>${esc(p.sales)||0} vendas</span>
                    </div>
                    <div class="affiliate-link-area" id="rank-link-${globalIdx}">
                        <span class="generating-link">Gerando link...</span>
                    </div>
                    <div class="social-btns">
                        <button class="btn btn-primary btn-sm" onclick="abrirPublishModal('rank',${globalIdx})">&#128227; Compartilhar</button>
                    </div>
                </div></div>`;
        });
        html += '</div>';
    });
    html += '</div>';

    // Chart
    const labels = [];
    const datasets = [];
    cats.forEach((cat, ci) => {
        const items = data[cat.key] || [];
        const colors = ['rgba(52,152,219,0.7)','rgba(238,77,45,0.7)','rgba(243,156,18,0.7)','rgba(39,174,96,0.7)'];
        const vals = items.slice(0, 10).map(p => { try { return parseFloat(p.commissionRate)*100; } catch { return 0; } });
        const names = items.slice(0, 10).map(p => (p.productName||'?').substring(0, 25));
        if (ci === 0) names.forEach(n => labels.push(n));
        datasets.push({ label: cat.title, data: vals, backgroundColor: colors[ci] });
    });

    html += `<div class="chart-container" style="margin-top:20px"><canvas id="rankChart"></canvas></div>`;
    document.getElementById('result-ranking').innerHTML = html;

    if (labels.length && typeof Chart !== 'undefined') {
        new Chart(document.getElementById('rankChart'), {
            type: 'bar',
            data: { labels, datasets },
            options: { responsive:true, scales:{ y:{ beginAtZero:true, title:{ display:true, text:'Comissão %' } } }, plugins:{ title:{ display:true, text:'Comparação de Comissão (Top 10)' } } }
        });
    }

    gerarLinksRanking(allRankProducts);
}

async function gerarLinksRanking(products) {
    const urls = products.map(p => p.offerLink || p.productLink).filter(Boolean);
    if (!urls.length) return;
    try {
        const r = await fetch('/api/short-links-lote', { method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({urls}) });
        if (r.status === 401) return;
        const data = await r.json();
        const sl = data.shortLinks || {};
        products.forEach((p, idx) => {
            const el = document.getElementById(`rank-link-${idx}`);
            if (!el) return;
            const orig = p.offerLink || p.productLink;
            const short = sl[orig];
            if (short) p._shortLink = short;
            const link = short || orig;
            if (link) {
                const safeLink = safeUrl(link);
                el.innerHTML = `<a class="ri-link ${short ? 'affiliate-short' : ''}" href="${safeLink}" target="_blank">${esc(link)}</a><button class="copy-btn" onclick="copyLink('${esc(link)}')">Copiar</button>`;
            } else {
                el.innerHTML = '<span class="link-fallback-note">Sem link</span>';
            }
        });
    } catch {
        products.forEach((p, idx) => {
            const el = document.getElementById(`rank-link-${idx}`);
            if (!el) return;
            const url = p.offerLink || p.productLink;
            if (url) {
                const safeLink = safeUrl(url);
                el.innerHTML = `<a class="ri-link" href="${safeLink}" target="_blank">${esc(url)}</a><button class="copy-btn" onclick="copyLink('${esc(url)}')">Copiar</button>`;
            }
        });
    }
}

// ─── Dashboard ───
async function carregarDashboard() {
    const s = document.getElementById('dash-start').value;
    const e = document.getElementById('dash-end').value;
    if (!s || !e) { showError('result-dashboard', 'Informe as datas.'); return; }
    showLoading('result-dashboard');

    try {
        const body = {
            purchaseTimeStart: Math.floor(new Date(s).getTime()/1000),
            purchaseTimeEnd: Math.floor(new Date(e+'T23:59:59').getTime()/1000),
            limit: 100,
        };
        const data = await apiCall('/api/relatorio-conversao', body);
        renderDashboard(data);
    } catch (err) { showError('result-dashboard', err); }
}

function renderDashboard(data) {
    const nodes = data.data?.conversionReport?.nodes || [];

    let totalComm = 0, totalOrders = 0, newBuyers = 0, appCount = 0, webCount = 0;
    const dailyMap = {};
    const utmMap = {};

    nodes.forEach(c => {
        const comm = parseFloat(c.totalCommission) || 0;
        totalComm += comm;
        if (c.buyerType === 'NEW') newBuyers++;
        if (c.device === 'APP') appCount++;
        if (c.device === 'WEB') webCount++;
        if (c.orders) {
            c.orders.forEach(() => { totalOrders++; });
        }
        if (c.purchaseTime) {
            const day = new Date(c.purchaseTime * 1000).toLocaleDateString('pt-BR');
            dailyMap[day] = (dailyMap[day] || 0) + comm;
        }
        const utm = c.utmContent || 'Sem tag';
        if (!utmMap[utm]) utmMap[utm] = { count: 0, commission: 0 };
        utmMap[utm].count++;
        utmMap[utm].commission += comm;
    });

    let html = `<div class="stats-grid">
        <div class="stat-card"><div class="stat-value">R$ ${totalComm.toFixed(2)}</div><div class="stat-label">Comissão Total</div></div>
        <div class="stat-card"><div class="stat-value">${nodes.length}</div><div class="stat-label">Conversões</div></div>
        <div class="stat-card"><div class="stat-value">${totalOrders}</div><div class="stat-label">Pedidos</div></div>
        <div class="stat-card"><div class="stat-value">${newBuyers}</div><div class="stat-label">Compradores Novos</div></div>
        <div class="stat-card"><div class="stat-value">${appCount}</div><div class="stat-label">Via App</div></div>
        <div class="stat-card"><div class="stat-value">${webCount}</div><div class="stat-label">Via Web</div></div>
    </div>`;

    const days = Object.keys(dailyMap).sort((a,b) => {
        const [da,ma,ya] = a.split('/'); const [db,mb,yb] = b.split('/');
        return new Date(ya,ma-1,da) - new Date(yb,mb-1,db);
    });
    const vals = days.map(d => dailyMap[d]);

    html += `<div class="chart-container"><canvas id="dashChart"></canvas></div>`;
    html += `<div style="display:grid;grid-template-columns:1fr 1fr;gap:16px">
        <div class="chart-container"><canvas id="deviceChart"></canvas></div>
        <div class="chart-container"><canvas id="buyerChart"></canvas></div>
    </div>`;

    const utmEntries = Object.entries(utmMap).sort((a,b) => b[1].commission - a[1].commission);
    if (utmEntries.length) {
        html += `<div class="chart-container" style="margin-top:16px">
            <h4 style="margin-bottom:12px;font-size:15px">Performance por Canal / SubID (utmContent)</h4>
            <div style="display:grid;grid-template-columns:1fr 1fr;gap:16px">
                <div><canvas id="utmChart"></canvas></div>
                <table class="historico-table">
                    <thead><tr><th>Canal / SubID</th><th>Conversões</th><th>Comissão R$</th></tr></thead><tbody>`;
        utmEntries.forEach(([utm, info]) => {
            html += `<tr><td><span style="background:var(--info);color:white;padding:1px 8px;border-radius:10px;font-size:11px">${esc(utm)}</span></td><td>${info.count}</td><td>R$ ${info.commission.toFixed(2)}</td></tr>`;
        });
        html += `</tbody></table></div></div>`;
    }

    html += buildJsonBlock(data, 'json-dashboard');
    document.getElementById('result-dashboard').innerHTML = html;

    if (typeof Chart !== 'undefined') {
        if (days.length) {
            new Chart(document.getElementById('dashChart'), {
                type: 'line',
                data: { labels: days, datasets: [{ label:'Comissão R$', data:vals, borderColor:'var(--primary)', backgroundColor:'rgba(238,77,45,0.1)', fill:true, tension:0.3 }] },
                options: { responsive:true, plugins:{ title:{ display:true, text:'Comissão por Dia' } } }
            });
        }
        new Chart(document.getElementById('deviceChart'), {
            type: 'doughnut',
            data: { labels:['App','Web'], datasets:[{ data:[appCount,webCount], backgroundColor:['rgba(52,152,219,0.8)','rgba(238,77,45,0.8)'] }] },
            options: { responsive:true, plugins:{ title:{ display:true, text:'Dispositivo' } } }
        });
        new Chart(document.getElementById('buyerChart'), {
            type: 'doughnut',
            data: { labels:['Novos','Recorrentes'], datasets:[{ data:[newBuyers, nodes.length-newBuyers], backgroundColor:['rgba(39,174,96,0.8)','rgba(243,156,18,0.8)'] }] },
            options: { responsive:true, plugins:{ title:{ display:true, text:'Tipo de Comprador' } } }
        });

        const utmCanvas = document.getElementById('utmChart');
        if (utmCanvas && utmEntries.length) {
            const utmColors = ['#EE4D2D','#3498DB','#27AE60','#F39C12','#9B59B6','#1ABC9C','#E74C3C','#2ECC71'];
            new Chart(utmCanvas, {
                type: 'bar',
                data: {
                    labels: utmEntries.map(([u]) => u.substring(0, 20)),
                    datasets: [{
                        label: 'Comissão R$',
                        data: utmEntries.map(([,i]) => i.commission),
                        backgroundColor: utmEntries.map((_, i) => utmColors[i % utmColors.length] + 'CC'),
                    }]
                },
                options: { responsive:true, indexAxis:'y', plugins:{ title:{ display:true, text:'Comissão por Canal' }, legend:{ display:false } } }
            });
        }
    }
}

// ─── Conversão ───
let convScrollId = null;
let convBody = {};

async function buscarConversao(useScroll) {
    const s = document.getElementById('conv-start').value;
    const e = document.getElementById('conv-end').value;
    if (!useScroll && (!s || !e)) { showError('result-conversao', 'Informe as datas.'); return; }
    showLoading('result-conversao');
    try {
        if (!useScroll) {
            convScrollId = null;
            convBody = {
                purchaseTimeStart: Math.floor(new Date(s).getTime()/1000),
                purchaseTimeEnd: Math.floor(new Date(e+'T23:59:59').getTime()/1000),
                orderStatus: document.getElementById('conv-status').value || undefined,
                buyerType: document.getElementById('conv-buyer').value || undefined,
                device: document.getElementById('conv-device').value || undefined,
                limit: parseInt(document.getElementById('conv-limit').value),
            };
        }
        const body = {...convBody};
        if (useScroll && convScrollId) body.scrollId = convScrollId;
        const data = await apiCall('/api/relatorio-conversao', body);
        renderConversao(data);
    } catch (e2) { showError('result-conversao', e2); }
}

function renderConversao(data) {
    const nodes = data.data?.conversionReport?.nodes || [];
    const pageInfo = data.data?.conversionReport?.pageInfo || {};
    convScrollId = pageInfo.scrollId || null;

    if (!nodes.length) { document.getElementById('result-conversao').innerHTML = '<div class="empty-state">Nenhuma conversão encontrada.</div>' + buildJsonBlock(data,'json-conv'); return; }
    let html = `<div class="results-header"><h3>Conversões</h3><span class="results-count">${nodes.length} resultado(s)</span></div>`;
    nodes.forEach(c => {
        const utmTag = c.utmContent ? `<span style="background:var(--info);color:white;padding:1px 8px;border-radius:10px;font-size:11px">${esc(c.utmContent)}</span>` : '';
        html += `<div class="offer-card"><h4>Conversão #${esc(c.conversionId)||'?'} ${utmTag}</h4>
            <div class="product-meta"><span>Comissão: R$ ${esc(c.totalCommission)||'?'}</span><span>${esc(c.buyerType)||'?'}</span><span>${esc(c.device)||'?'}</span><span>Compra: ${formatDate(c.purchaseTime)}</span></div>`;
        if (c.orders) c.orders.forEach(o => {
            html += `<div style="margin-top:8px;padding-left:12px;border-left:3px solid var(--primary)"><div class="product-meta"><span>Pedido: ${esc(o.orderId)}</span><span>Status: ${esc(o.orderStatus)}</span></div>`;
            if (o.items) o.items.forEach(it => html += `<div class="product-meta" style="margin-top:4px"><span>${esc(it.itemName)||'?'}</span><span>R$${esc(it.itemPrice)||'?'} x${it.qty||1}</span><span>Com: R$${esc(it.itemTotalCommission)||'?'}</span></div>`);
            html += '</div>';
        });
        html += '</div>';
    });

    if (pageInfo.hasNextPage && convScrollId) {
        html += `<div class="pagination">
            <span class="page-info" style="color:var(--warning);font-size:12px">scrollId expira em 30s</span>
            <button class="btn btn-primary btn-sm" onclick="buscarConversao(true)">Carregar Próxima Página</button>
        </div>`;
    }

    html += buildJsonBlock(data,'json-conv');
    document.getElementById('result-conversao').innerHTML = html;
}

// ─── Validado ───
let valScrollId = null;
let valBody = {};

async function buscarValidado(useScroll) {
    const id = document.getElementById('val-id').value;
    if (!useScroll && !id) { showError('result-validado', 'Informe o Validation ID.'); return; }
    showLoading('result-validado');
    try {
        if (!useScroll) {
            valScrollId = null;
            valBody = { validationId:parseInt(id), limit:parseInt(document.getElementById('val-limit').value) };
        }
        const body = {...valBody};
        if (useScroll && valScrollId) body.scrollId = valScrollId;

        const data = await apiCall('/api/relatorio-validado', body);
        const nodes = data.data?.validatedReport?.nodes || [];
        const pageInfo = data.data?.validatedReport?.pageInfo || {};
        valScrollId = pageInfo.scrollId || null;

        let html = `<div class="results-header"><h3>Relatório Validado</h3><span class="results-count">${nodes.length} resultado(s)</span></div>`;
        if (!nodes.length) html += '<div class="empty-state">Nenhum registro.</div>';
        else nodes.forEach(v => {
            html += `<div class="offer-card"><h4>Conversão #${esc(v.conversionId)||'?'}</h4>
                <div class="product-meta"><span>Líquida: R$ ${esc(v.netCommission)||'?'}</span><span>Total: R$ ${esc(v.totalCommission)||'?'}</span></div>`;
            if (v.orders) v.orders.forEach(o => {
                html += `<div style="margin-top:8px;padding-left:12px;border-left:3px solid var(--success)"><div class="product-meta"><span>Pedido: ${esc(o.orderId)}</span></div>`;
                if (o.items) o.items.forEach(it => html += `<div class="product-meta" style="margin-top:4px"><span>${esc(it.itemName)||'?'}</span><span>Com: R$${esc(it.itemTotalCommission)||'?'}</span>${it.refundAmount?`<span style="color:var(--danger)">Reemb: R$${esc(it.refundAmount)}</span>`:''}</div>`);
                html += '</div>';
            });
            html += '</div>';
        });

        if (pageInfo.hasNextPage && valScrollId) {
            html += `<div class="pagination">
                <span class="page-info" style="color:var(--warning);font-size:12px">scrollId expira em 30s</span>
                <button class="btn btn-primary btn-sm" onclick="buscarValidado(true)">Carregar Próxima Página</button>
            </div>`;
        }

        html += buildJsonBlock(data,'json-val');
        document.getElementById('result-validado').innerHTML = html;
    } catch (e) { showError('result-validado', e); }
}

// ─── Histórico ───
async function carregarHistorico() {
    showLoading('result-historico');
    try {
        const r = await fetch('/api/historico');
        if (r.status === 401) { window.location.href = '/login'; return; }
        const hist = await r.json();
        if (!hist.length) { document.getElementById('result-historico').innerHTML = '<div class="empty-state">Nenhum registro no histórico. Busque produtos e clique em "Salvar no Histórico".</div>'; return; }

        const snapshots = {};
        hist.forEach(h => { const ts = h.timestamp; if (!snapshots[ts]) snapshots[ts] = []; snapshots[ts].push(h); });
        const tsKeys = Object.keys(snapshots).sort((a,b) => b-a);

        let html = `<div class="results-header"><h3>Histórico de Snapshots</h3><span class="results-count">${tsKeys.length} snapshot(s), ${hist.length} registro(s)</span></div>`;

        const productMap = {};
        hist.forEach(h => {
            const name = (h.productName||'?').substring(0, 30);
            if (!productMap[name]) productMap[name] = [];
            productMap[name].push({ ts: h.timestamp, rate: parseFloat(h.commissionRate||0)*100 });
        });
        const topProducts = Object.entries(productMap).sort((a,b) => b[1].length - a[1].length).slice(0, 5);

        if (topProducts.length && tsKeys.length > 1) {
            html += `<div class="chart-container"><canvas id="histChart"></canvas></div>`;
        }

        tsKeys.forEach(ts => {
            const items = snapshots[ts];
            html += `<h4 style="margin:16px 0 8px;color:var(--text-secondary)">Snapshot: ${formatDateTime(parseInt(ts))} (${items.length} produtos)</h4>`;
            html += `<table class="historico-table"><thead><tr><th>Produto</th><th>Preço Mín</th><th>Preço Máx</th><th>Comissão %</th><th>Comissão R$</th><th>Vendas</th><th>Loja</th></tr></thead><tbody>`;
            items.forEach(h => {
                html += `<tr><td>${esc(h.productName)||'?'}</td><td>R$ ${esc(h.priceMin)||'?'}</td><td>R$ ${esc(h.priceMax)||'?'}</td><td>${formatComm(h.commissionRate)}</td><td>R$ ${esc(h.commission)||'?'}</td><td>${esc(h.sales)||'?'}</td><td>${esc(h.shopName)||'?'}</td></tr>`;
            });
            html += '</tbody></table>';
        });

        document.getElementById('result-historico').innerHTML = html;

        const canvas = document.getElementById('histChart');
        if (canvas && topProducts.length && typeof Chart !== 'undefined') {
            const sortedKeys = Object.keys(snapshots).sort();
            const chartLabels = sortedKeys.map(ts => formatDateTime(parseInt(ts)));
            const colors = ['#EE4D2D','#3498DB','#27AE60','#F39C12','#9B59B6'];
            const datasets = topProducts.map(([name, entries], i) => {
                const dataMap = {};
                entries.forEach(e => dataMap[e.ts] = e.rate);
                return { label:name, data:sortedKeys.map(ts => dataMap[ts]||null), borderColor:colors[i], tension:0.3, spanGaps:true };
            });
            new Chart(canvas, {
                type:'line',
                data:{ labels:chartLabels, datasets },
                options:{ responsive:true, plugins:{ title:{ display:true, text:'Evolução da Comissão (%)' } } }
            });
        }
    } catch (e) { showError('result-historico', e); }
}

async function limparHistorico() {
    if (!confirm('Limpar todo o histórico?')) return;
    await fetch('/api/historico', { method:'DELETE' });
    document.getElementById('result-historico').innerHTML = '<div class="empty-state">Histórico limpo.</div>';
}

// ─── Multi-Channel Publishing ───
let channelStatuses = {};
let channelTexts = {};
let selectedChannels = [];
let activeChannelTab = null;

const CHANNEL_ICONS = {
    telegram: '&#9992;',
    whatsapp: '&#128172;',
    instagram_feed: '&#128247;',
    instagram_stories: '&#127912;',
};

async function checkChannelStatuses() {
    try {
        const r = await fetch('/api/channels/status');
        if (r.status === 401) return;
        const data = await r.json();
        // Separate gemini flag from channel statuses
        const gemini = data.gemini;
        delete data.gemini;
        channelStatuses = data;
        renderChannelCheckboxes();
    } catch { /* ignore */ }
}

function renderChannelCheckboxes() {
    const container = document.getElementById('channel-selector');
    if (!container) return;
    let html = '';
    for (const [name, status] of Object.entries(channelStatuses)) {
        const icon = CHANNEL_ICONS[name] || '&#128276;';
        const disabled = !status.configured ? 'disabled' : '';
        const checked = status.configured && status.connected ? 'checked' : '';
        const label = esc(status.displayName || name);
        const dotClass = status.connected ? 'dot-green' : (status.configured ? 'dot-yellow' : 'dot-red');
        html += `<label class="channel-checkbox ${disabled ? 'channel-disabled' : ''}">
            <input type="checkbox" value="${esc(name)}" ${checked} ${disabled}
                   onchange="onChannelToggle()">
            <span class="channel-icon">${icon}</span>
            <span>${label}</span>
            <span class="channel-status-dot ${dotClass}"></span>
        </label>`;
    }
    container.innerHTML = html;
}

function onChannelToggle() {
    selectedChannels = [...document.querySelectorAll('#channel-selector input:checked')]
        .map(cb => cb.value);
    renderChannelTabs();
    if (selectedChannels.length && !selectedChannels.includes(activeChannelTab)) {
        activateChannelTab(selectedChannels[0]);
    }
}

function renderChannelTabs() {
    const tabsEl = document.getElementById('channel-tabs');
    if (!tabsEl || !selectedChannels.length) {
        if (tabsEl) tabsEl.innerHTML = '';
        return;
    }
    tabsEl.innerHTML = selectedChannels.map(ch => {
        const label = channelStatuses[ch]?.displayName || ch;
        return `<div class="ch-tab ${ch === activeChannelTab ? 'active' : ''}"
              onclick="activateChannelTab('${esc(ch)}')">${esc(label)}</div>`;
    }).join('');
}

function activateChannelTab(channel) {
    // Save current tab text before switching
    if (activeChannelTab) {
        channelTexts[activeChannelTab] = document.getElementById('channel-manual-text').value;
    }
    activeChannelTab = channel;
    const textarea = document.getElementById('channel-manual-text');
    const preview = document.getElementById('channel-preview-text');
    textarea.value = channelTexts[channel] || '';
    preview.textContent = channelTexts[channel] || '';
    renderChannelTabs();
}

function _pubGetPool(source) {
    return { produto: lastProductNodes, rank: lastRankNodes, loja: lastLojaNodes, oferta: lastOfertaNodes }[source] || lastProductNodes;
}

function abrirPublishModal(source, idx) {
    const p = _pubGetPool(source)[idx];
    if (!p) return;
    window._pubCurrentItem = p;

    const modal = document.getElementById('publish-modal-overlay');
    const link = p._shortLink || p.offerLink || p.productLink || '';
    const img = p.imageUrl || '';
    const nome = p.productName || p.shopName || p.offerName || 'Item';

    document.getElementById('pub-prod-name').textContent = nome;
    document.getElementById('pub-prod-img').src = safeUrl(img) || '';
    document.getElementById('pub-prod-img').style.display = img ? 'block' : 'none';
    document.getElementById('pub-prod-preco').innerHTML = formatPrice(p);
    document.getElementById('pub-prod-link').textContent = link;
    document.getElementById('channel-preview-text').textContent = 'Selecione os canais e clique em "Gerar com IA"';
    document.getElementById('channel-manual-text').value = '';
    document.getElementById('publish-status-msg').innerHTML = '';
    document.getElementById('publish-status-msg').className = 'tg-status';

    // Reset state
    channelTexts = {};
    activeChannelTab = null;

    modal.classList.add('active');
    checkChannelStatuses();
}

function fecharPublishModal() {
    document.getElementById('publish-modal-overlay').classList.remove('active');
}

async function gerarPreviewCanais() {
    selectedChannels = [...document.querySelectorAll('#channel-selector input:checked')]
        .map(cb => cb.value);
    if (!selectedChannels.length) {
        document.getElementById('channel-preview-text').textContent = 'Selecione pelo menos um canal.';
        return;
    }

    const p = window._pubCurrentItem;
    if (!p) return;

    const btnPreview = document.getElementById('pub-btn-preview');
    const previewEl = document.getElementById('channel-preview-text');
    btnPreview.disabled = true;
    btnPreview.textContent = 'Gerando com IA...';
    previewEl.textContent = 'Aguarde, o Gemini esta criando os anuncios...';

    const produto = { ...p, shortLink: p._shortLink || '' };

    try {
        const r = await fetch('/api/channels/preview', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ produto, canais: selectedChannels })
        });
        if (r.status === 401) { window.location.href = '/login'; return; }
        const data = await r.json();

        channelTexts = {};
        for (const canal of selectedChannels) {
            channelTexts[canal] = data[canal]?.texto || data[canal]?.error || '';
        }

        renderChannelTabs();
        activateChannelTab(selectedChannels[0]);
    } catch (e) {
        previewEl.textContent = 'Erro ao gerar preview: ' + e.message;
    }
    btnPreview.disabled = false;
    btnPreview.innerHTML = '&#x21BA; Gerar com IA';
}

async function publicarNosCanais() {
    const p = window._pubCurrentItem;
    if (!p) return;

    // Save current tab text
    if (activeChannelTab) {
        channelTexts[activeChannelTab] = document.getElementById('channel-manual-text').value;
    }

    const publicacoes = selectedChannels
        .filter(ch => channelTexts[ch]?.trim())
        .map(ch => ({ canal: ch, texto: channelTexts[ch] }));

    if (!publicacoes.length) {
        const statusEl = document.getElementById('publish-status-msg');
        statusEl.className = 'tg-status tg-error';
        statusEl.textContent = 'Gere o anuncio com IA primeiro ou escreva um texto manualmente.';
        return;
    }

    const btnEnviar = document.getElementById('pub-btn-enviar');
    const statusEl = document.getElementById('publish-status-msg');
    btnEnviar.disabled = true;
    btnEnviar.textContent = 'Publicando...';
    statusEl.className = 'tg-status';
    statusEl.textContent = 'Publicando nos canais selecionados...';

    const produto = { ...p, shortLink: p._shortLink || '' };

    try {
        const r = await fetch('/api/channels/publish', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ produto, publicacoes })
        });
        if (r.status === 401) { window.location.href = '/login'; return; }
        const data = await r.json();

        // Show per-channel results
        let allSuccess = true;
        let resultHtml = '';
        for (const [canal, result] of Object.entries(data)) {
            const icon = result.success ? '&#9989;' : '&#10060;';
            const cls = result.success ? 'tg-success' : 'tg-error';
            const displayName = channelStatuses[canal]?.displayName || canal;
            const msg = result.success ? result.message : (result.error || 'Erro desconhecido');
            resultHtml += `<div class="tg-status ${cls}" style="margin-top:4px">${icon} ${esc(displayName)}: ${esc(msg)}</div>`;
            if (!result.success) allSuccess = false;
        }
        statusEl.innerHTML = resultHtml;
        statusEl.className = '';
    } catch (e) {
        statusEl.className = 'tg-status tg-error';
        statusEl.textContent = 'Erro: ' + e.message;
    }
    btnEnviar.disabled = false;
    btnEnviar.innerHTML = '&#128227; Publicar';
}

// ─── Configuracoes (Settings) ───

async function carregarCredenciais() {
    try {
        const r = await fetch('/api/settings/credentials');
        if (r.status === 401) { window.location.href = '/login'; return; }
        const data = await r.json();

        // Preenche campos com valores mascarados (ou vazio)
        const services = {
            shopee: ['app_id', 'secret'],
            gemini: ['api_key'],
            telegram: ['bot_token', 'chat_id'],
            instagram: ['access_token', 'user_id'],
        };

        for (const [service, fields] of Object.entries(services)) {
            const creds = data[service] || {};
            for (const field of fields) {
                const el = document.getElementById(`cred-${service}-${field}`);
                if (el) {
                    el.value = '';
                    el.placeholder = creds[field] || 'Nao configurado';
                }
            }
        }

        // Carregar status do WhatsApp separadamente
        verificarStatusWhatsApp();
    } catch (e) {
        const statusEl = document.getElementById('settings-status-msg');
        if (statusEl) {
            statusEl.className = 'tg-status tg-error';
            statusEl.textContent = 'Erro ao carregar credenciais: ' + e.message;
        }
    }
}

async function salvarCredencial(service, fields) {
    const statusEl = document.getElementById('settings-status-msg');
    const creds = {};
    let hasValue = false;

    for (const field of fields) {
        const el = document.getElementById(`cred-${service}-${field}`);
        if (el && el.value.trim()) {
            creds[field] = el.value.trim();
            hasValue = true;
        }
    }

    if (!hasValue) {
        statusEl.className = 'tg-status tg-error';
        statusEl.textContent = 'Preencha pelo menos um campo antes de salvar.';
        return;
    }

    try {
        const r = await fetch(`/api/settings/credentials/${service}`, {
            method: 'PUT',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(creds),
        });
        if (r.status === 401) { window.location.href = '/login'; return; }
        const data = await r.json();

        if (data.saved) {
            statusEl.className = 'tg-status tg-success';
            statusEl.textContent = `Credenciais de ${service} salvas com sucesso!`;
            carregarCredenciais();
        } else {
            statusEl.className = 'tg-status tg-error';
            statusEl.textContent = data.error || 'Erro ao salvar.';
        }
    } catch (e) {
        statusEl.className = 'tg-status tg-error';
        statusEl.textContent = 'Erro: ' + e.message;
    }
}

async function removerCredencial(service) {
    if (!confirm(`Remover credenciais de ${service}?`)) return;

    const statusEl = document.getElementById('settings-status-msg');
    try {
        const r = await fetch(`/api/settings/credentials/${service}`, { method: 'DELETE' });
        if (r.status === 401) { window.location.href = '/login'; return; }

        statusEl.className = 'tg-status tg-success';
        statusEl.textContent = `Credenciais de ${service} removidas.`;
        carregarCredenciais();
    } catch (e) {
        statusEl.className = 'tg-status tg-error';
        statusEl.textContent = 'Erro: ' + e.message;
    }
}

// ─── WhatsApp Evolution API ───

let _waQrPollInterval = null;

async function verificarStatusWhatsApp() {
    const dotEl = document.getElementById('wa-status-dot');
    const textEl = document.getElementById('wa-status-text');
    const instanceEl = document.getElementById('wa-instance-name');
    const btnConnect = document.getElementById('wa-btn-connect');
    const btnGroups = document.getElementById('wa-btn-groups');
    const btnDisconnect = document.getElementById('wa-btn-disconnect');
    const groupInfo = document.getElementById('wa-group-info');
    const selectedGroup = document.getElementById('wa-selected-group');

    try {
        const r = await fetch('/api/whatsapp/status');
        const data = await r.json();

        if (data.status === 'not_configured') {
            dotEl.className = 'wa-status-dot dot-red';
            textEl.textContent = 'Evolution API nao configurada no servidor';
            btnConnect.style.display = 'none';
            return;
        }

        instanceEl.textContent = data.instanceName ? `Instancia: ${data.instanceName}` : '';

        if (data.status === 'open') {
            dotEl.className = 'wa-status-dot dot-green';
            textEl.textContent = 'Conectado';
            btnConnect.style.display = 'none';
            btnGroups.style.display = '';
            btnDisconnect.style.display = '';

            if (data.groupId) {
                groupInfo.style.display = '';
                selectedGroup.textContent = data.groupName || data.groupId;
            } else {
                groupInfo.style.display = '';
                selectedGroup.textContent = 'Nenhum grupo selecionado - clique em "Selecionar Grupo"';
            }
        } else if (data.status === 'connecting') {
            dotEl.className = 'wa-status-dot dot-yellow';
            textEl.textContent = 'Conectando...';
            btnConnect.textContent = 'Reconectar';
            btnConnect.style.display = '';
            btnGroups.style.display = 'none';
            btnDisconnect.style.display = '';
            groupInfo.style.display = 'none';
        } else {
            dotEl.className = 'wa-status-dot dot-red';
            textEl.textContent = data.status === 'not_created' ? 'Nao conectado' : 'Desconectado';
            btnConnect.textContent = 'Conectar WhatsApp';
            btnConnect.style.display = '';
            btnGroups.style.display = 'none';
            btnDisconnect.style.display = 'none';
            groupInfo.style.display = 'none';
        }
    } catch (e) {
        dotEl.className = 'wa-status-dot dot-red';
        textEl.textContent = 'Erro ao verificar status';
    }
}

async function conectarWhatsApp() {
    const qrArea = document.getElementById('wa-qr-area');
    const qrContainer = document.getElementById('wa-qr-container');
    const btnConnect = document.getElementById('wa-btn-connect');

    btnConnect.disabled = true;
    btnConnect.textContent = 'Conectando...';
    qrArea.style.display = '';
    qrContainer.innerHTML = '<div class="loading"><div class="spinner"></div><p>Gerando QR Code...</p></div>';

    try {
        const r = await fetch('/api/whatsapp/connect', { method: 'POST' });
        const data = await r.json();

        if (data.error) {
            qrContainer.innerHTML = `<div class="error-msg"><strong>Erro</strong>${esc(data.error)}</div>`;
            btnConnect.disabled = false;
            btnConnect.textContent = 'Tentar novamente';
            return;
        }

        if (data.status === 'connected') {
            qrArea.style.display = 'none';
            btnConnect.disabled = false;
            verificarStatusWhatsApp();
            return;
        }

        // Exibir QR code
        if (data.qrCode) {
            const imgSrc = data.qrCode.startsWith('data:') ? data.qrCode : 'data:image/png;base64,' + data.qrCode;
            qrContainer.innerHTML = `<img src="${imgSrc}" alt="QR Code WhatsApp" class="wa-qr-img">`;

            if (data.pairingCode) {
                qrContainer.innerHTML += `<div class="wa-pairing-code">Codigo de pareamento: <strong>${esc(data.pairingCode)}</strong></div>`;
            }
        } else {
            qrContainer.innerHTML = '<div class="error-msg">QR Code nao recebido. Tente novamente.</div>';
        }

        // Iniciar polling de status
        iniciarPollingConexao();
    } catch (e) {
        qrContainer.innerHTML = `<div class="error-msg"><strong>Erro</strong>${esc(e.message)}</div>`;
    }

    btnConnect.disabled = false;
    btnConnect.textContent = 'Reconectar';
}

function iniciarPollingConexao() {
    pararPollingConexao();
    _waQrPollInterval = setInterval(async () => {
        try {
            const r = await fetch('/api/whatsapp/status');
            const data = await r.json();

            if (data.status === 'open') {
                pararPollingConexao();
                document.getElementById('wa-qr-area').style.display = 'none';
                verificarStatusWhatsApp();

                const statusEl = document.getElementById('settings-status-msg');
                statusEl.className = 'tg-status tg-success';
                statusEl.textContent = 'WhatsApp conectado com sucesso!';
            }
        } catch (e) {
            // ignora erros de polling
        }
    }, 3000);

    // Para apos 2 minutos (QR expira)
    setTimeout(() => pararPollingConexao(), 120000);
}

function pararPollingConexao() {
    if (_waQrPollInterval) {
        clearInterval(_waQrPollInterval);
        _waQrPollInterval = null;
    }
}

async function listarGruposWhatsApp() {
    const container = document.getElementById('wa-groups-container');
    const listArea = document.getElementById('wa-groups-list');
    listArea.style.display = '';
    container.innerHTML = '<div class="loading"><div class="spinner"></div><p>Carregando grupos...</p></div>';

    try {
        const r = await fetch('/api/whatsapp/groups');
        const data = await r.json();

        if (data.error) {
            container.innerHTML = `<div class="error-msg">${esc(data.error)}</div>`;
            return;
        }

        const groups = data.groups || [];
        if (groups.length === 0) {
            container.innerHTML = '<div class="empty-state">Nenhum grupo encontrado. Crie um grupo no WhatsApp primeiro.</div>';
            return;
        }

        let html = '';
        for (const g of groups) {
            html += `<div class="wa-group-item" onclick="selecionarGrupoWhatsApp('${esc(g.id)}', '${esc(g.subject)}')">
                <span class="wa-group-name">${esc(g.subject)}</span>
                <span class="wa-group-size">${g.size} membros</span>
            </div>`;
        }
        container.innerHTML = html;
    } catch (e) {
        container.innerHTML = `<div class="error-msg">Erro: ${esc(e.message)}</div>`;
    }
}

async function selecionarGrupoWhatsApp(groupId, groupName) {
    try {
        const r = await fetch('/api/whatsapp/select-group', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ groupId, groupName }),
        });
        const data = await r.json();

        if (data.saved) {
            document.getElementById('wa-groups-list').style.display = 'none';
            document.getElementById('wa-selected-group').textContent = groupName;
            document.getElementById('wa-group-info').style.display = '';

            const statusEl = document.getElementById('settings-status-msg');
            statusEl.className = 'tg-status tg-success';
            statusEl.textContent = `Grupo "${groupName}" selecionado com sucesso!`;
        } else {
            const statusEl = document.getElementById('settings-status-msg');
            statusEl.className = 'tg-status tg-error';
            statusEl.textContent = data.error || 'Erro ao selecionar grupo.';
        }
    } catch (e) {
        const statusEl = document.getElementById('settings-status-msg');
        statusEl.className = 'tg-status tg-error';
        statusEl.textContent = 'Erro: ' + e.message;
    }
}

async function desconectarWhatsApp() {
    if (!confirm('Desconectar seu WhatsApp? Voce precisara escanear o QR Code novamente.')) return;

    try {
        const r = await fetch('/api/whatsapp/disconnect', { method: 'POST' });
        const data = await r.json();

        if (data.disconnected) {
            const statusEl = document.getElementById('settings-status-msg');
            statusEl.className = 'tg-status tg-success';
            statusEl.textContent = 'WhatsApp desconectado.';
            verificarStatusWhatsApp();
        } else {
            const statusEl = document.getElementById('settings-status-msg');
            statusEl.className = 'tg-status tg-error';
            statusEl.textContent = data.error || 'Erro ao desconectar.';
        }
    } catch (e) {
        const statusEl = document.getElementById('settings-status-msg');
        statusEl.className = 'tg-status tg-error';
        statusEl.textContent = 'Erro: ' + e.message;
    }
}

// ─── Init ───
(function() {
    const today = new Date().toISOString().split('T')[0];
    const weekAgo = new Date(Date.now() - 7*86400000).toISOString().split('T')[0];
    document.getElementById('conv-start').value = weekAgo;
    document.getElementById('conv-end').value = today;
    document.getElementById('dash-start').value = weekAgo;
    document.getElementById('dash-end').value = today;
})();
