const $ = (id) => document.getElementById(id);
const VIEW_LABELS = { front: 'Спереди', right: 'Сбоку (справа)', back: 'Сзади', left: 'Сбоку (слева)' };
const PEOPLE_NAMES = { 1: 'портрет', 2: 'пара' };
const GROWTH_PLACEHOLDERS = ['Например: 1 год', 'Например: 5 лет', 'Например: 10 лет'];

const state = {
  product: 'sculpture', config: null, style: null,
  photo: null, growth: [],            // growth: [{data, name, label}]
  extra: {},                          // необязательные фото сбоку/сзади: {left, right, back} (data URL)
  people: 1, elements: 0,
  generationIds: [], address: null, delivery: null,
};

const fmt = (n) => n.toLocaleString('ru-RU') + ' ₽';
const isGrowth = () => state.product === 'growth';
const growthCount = () => (state.config ? state.config.growthCount : 3);

function figurePrice() {
  const c = state.config;
  if (isGrowth()) return c.priceGrowth;
  const base = state.people === 1 ? c.price1 : c.price2 + (state.people - 2) * c.step;
  return base;
}

function setError(id, msg) { $(id).textContent = msg || ''; }

// Меняется продукт, состав, стиль или фото — старый макет уже не подходит.
function invalidate() {
  if (!state.generationIds.length) return;
  state.generationIds = [];
  $('p3').style.display = 'none';
  $('p4').style.display = 'none';
  $('gen').textContent = 'Показать макет';
  setError('err2', 'Данные изменились — создайте макет заново.');
}

function photosReady() {
  return isGrowth()
    ? state.growth.filter(Boolean).length === growthCount()
    : !!state.photo;
}
function refreshLock() { $('p2').classList.toggle('locked', !photosReady()); }

function renderProduct() {
  const g = isGrowth();
  $('photoSingle').hidden = g;
  $('photoGrowth').hidden = !g;
  $('compoBlock').hidden = g;
  $('photoTitle').textContent = g ? `Загрузите ${growthCount()} фото` : 'Загрузите фото';
  $('styleTitle').textContent = g ? 'Характер исполнения' : 'Состав и характер исполнения';
  $('liveLabel').innerHTML = (g ? `Набор из ${growthCount()} фигурок` : 'Стоимость скульптуры') + ' <small class="muted">(доставка отдельно)</small>';
  $('livePrice').textContent = fmt(figurePrice());
  refreshLock();
  updateTotal();
}

function renderComposition() {
  const c = state.config;
  $('livePrice').textContent = fmt(figurePrice());
  updateTotal();
}

function updateTotal() {
  if (!state.config) return;
  const ship = state.delivery ? state.delivery.price : null;
  const price = figurePrice();
  const what = isGrowth() ? 'набор' : 'скульптура';
  $('total').firstChild.textContent = fmt(price + (ship || 0));
  $('totalNote').textContent = ship === null
    ? `${what} ${fmt(price)} + доставка (выберите адрес)`
    : `${what} ${fmt(price)} + доставка ${fmt(ship)}`;
}

// ---------- загрузка конфигурации ----------
fetch('/api/config').then((r) => r.json()).then((c) => {
  state.config = c;
  $('prodSculpt').textContent = `1–${c.maxPeople} человек, от ${fmt(c.price1)}`;
  $('prodGrowth').textContent = `${c.growthCount} фигурки одного ребёнка в разном возрасте · ${fmt(c.priceGrowth)}`;

  const pick = $('peoplePick');
  for (let n = 1; n <= c.maxPeople; n++) {
    const l = document.createElement('label');
    const word = n === 1 ? 'человек' : n < 5 ? 'человека' : 'человек';
    l.innerHTML = `<input type="radio" name="people" value="${n}"><span>${n} ${word}<small>${PEOPLE_NAMES[n] || 'семья'}</small></span>`;
    pick.appendChild(l);
  }
  const params = new URLSearchParams(location.search);
  state.people = Math.min(c.maxPeople, Math.max(1, parseInt(params.get('people')) || 1));
  pick.querySelector(`input[value="${state.people}"]`).checked = true;
  pick.addEventListener('change', (e) => { state.people = +e.target.value; invalidate(); renderComposition(); });

  const box = $('styles');
  Object.entries(c.styles).forEach(([key, title], i) => {
    const label = document.createElement('label');
    label.className = 'style-opt';
    label.innerHTML = `<input type="radio" name="style" value="${key}"${i === 0 ? ' checked' : ''}>
      <img src="/static/img/styles/${key}.jpg" alt="${title}" loading="lazy"><span>${title}</span>`;
    box.appendChild(label);
  });
  state.style = Object.keys(c.styles)[0];
  box.addEventListener('change', (e) => { state.style = e.target.value; invalidate(); });

  $('demoAddr').hidden = !c.addressDemo;
  if (c.addressDemo) $('address').placeholder = 'Улица, дом';

  // слоты для «Этапов взросления»
  const slots = $('growthSlots');
  for (let i = 0; i < c.growthCount; i++) {
    const d = document.createElement('div');
    d.className = 'gslot';
    d.innerHTML = `<label class="gdrop" for="gfile${i}"><span class="gplus">+</span><span>Фото ${i + 1}</span></label>
      <input type="file" id="gfile${i}" accept="image/jpeg,image/png,image/webp" hidden>
      <img class="gpreview" alt="" hidden>
      <button type="button" class="gremove" hidden aria-label="Убрать фото">✕</button>
      <input type="text" class="glabel" maxlength="40" placeholder="${GROWTH_PLACEHOLDERS[i] || 'Возраст'}" aria-label="Подпись к фото ${i + 1}">`;
    slots.appendChild(d);
    d.querySelector('input[type=file]').addEventListener('change', (e) => handleGrowthFile(i, e.target.files[0], d));
    d.querySelector('.glabel').addEventListener('input', (e) => { if (state.growth[i]) state.growth[i].label = e.target.value; invalidate(); });
    d.querySelector('.gremove').addEventListener('click', () => removeGrowth(i, d));
    onDropFiles(d, (f) => handleGrowthFile(i, f, d));
  }

  const startProduct = params.get('product') === 'growth' || params.get('idea') === 'growth';
  if (startProduct) document.querySelector('input[name=product][value=growth]').checked = true;
  state.product = startProduct ? 'growth' : 'sculpture';
  renderComposition();
  renderProduct();
});

document.querySelectorAll('input[name=product]').forEach((r) => r.addEventListener('change', (e) => {
  state.product = e.target.value;
  state.delivery = null;
  invalidate();
  renderProduct();
}));

// ---------- фото ----------
// Уменьшаем фото в браузере: быстрее загрузка, и заодно убираются метаданные (EXIF, геопозиция).
function resizeToDataUrl(file, maxSide = 1600) {
  return new Promise((resolve, reject) => {
    const url = URL.createObjectURL(file);
    const img = new Image();
    img.onload = () => {
      const k = Math.min(1, maxSide / Math.max(img.width, img.height));
      const c = document.createElement('canvas');
      c.width = Math.round(img.width * k);
      c.height = Math.round(img.height * k);
      c.getContext('2d').drawImage(img, 0, 0, c.width, c.height);
      URL.revokeObjectURL(url);
      resolve(c.toDataURL('image/jpeg', 0.9));
    };
    img.onerror = () => { URL.revokeObjectURL(url); reject(new Error('Не удалось прочитать изображение')); };
    img.src = url;
  });
}

function checkFile(file) {
  if (!file) return 'Файл не выбран.';
  if (!/^image\/(jpeg|png|webp)$/.test(file.type)) return 'Подойдут только JPG, PNG или WebP.';
  if (file.size > 25 * 1024 * 1024) return 'Файл слишком большой (максимум 25 МБ).';
  return '';
}

// Зона принимает файлы перетаскиванием: бросили файл — он загрузился или заменил прежний.
function onDropFiles(el, handler) {
  ['dragenter', 'dragover'].forEach((ev) => el.addEventListener(ev, (e) => { e.preventDefault(); el.classList.add('over'); }));
  el.addEventListener('dragleave', (e) => { if (!el.contains(e.relatedTarget)) el.classList.remove('over'); });
  el.addEventListener('drop', (e) => {
    e.preventDefault(); e.stopPropagation(); el.classList.remove('over');
    handler(e.dataTransfer.files[0]);
  });
}
// Промахнувшийся файл не должен открываться в браузере вместо страницы.
['dragover', 'drop'].forEach((ev) => window.addEventListener(ev, (e) => e.preventDefault()));

// --- основное фото
async function handleFile(file) {
  setError('err1');
  const bad = checkFile(file);
  if (bad) return file && setError('err1', bad);
  try { state.photo = await resizeToDataUrl(file); } catch (e) { return setError('err1', e.message); }
  invalidate();
  $('previewImg').src = state.photo;
  $('fileName').textContent = file.name;
  $('drop').style.display = 'none';
  $('preview').style.display = 'flex';
  refreshLock();   // страницу не прокручиваем: рядом блок про фото с других сторон, его важно увидеть
}

function removePhoto() {
  state.photo = null;
  $('file').value = '';
  $('previewImg').removeAttribute('src');
  $('preview').style.display = 'none';
  $('drop').style.display = '';
  setError('err1');
  invalidate();
  refreshLock();
}

$('file').addEventListener('change', (e) => { handleFile(e.target.files[0]); e.target.value = ''; });
$('changePhoto').addEventListener('click', () => $('file').click());
$('removePhoto').addEventListener('click', removePhoto);
onDropFiles($('drop'), handleFile);
onDropFiles($('preview'), handleFile);   // фото можно заменить, просто бросив другое на превью

// --- необязательные фото с других сторон
const ANGLES = [['left', 'Слева'], ['right', 'Справа'], ['back', 'Сзади']];
ANGLES.forEach(([side, title]) => {
  const d = document.createElement('div');
  d.className = 'aslot';
  d.innerHTML = `<label class="adrop" for="afile-${side}"><span class="aplus">+</span><span>${title}</span></label>
    <input type="file" id="afile-${side}" accept="image/jpeg,image/png,image/webp" hidden>
    <img class="apreview" alt="Фото: ${title.toLowerCase()}" hidden>
    <button type="button" class="aremove" hidden aria-label="Убрать фото">✕</button>`;
  $('angleSlots').appendChild(d);
  d.querySelector('input').addEventListener('change', (e) => { handleExtra(side, e.target.files[0], d); e.target.value = ''; });
  d.querySelector('.aremove').addEventListener('click', () => removeExtra(side, d));
  onDropFiles(d, (f) => handleExtra(side, f, d));
});

async function handleExtra(side, file, slot) {
  setError('err1');
  const bad = checkFile(file);
  if (bad) return file && setError('err1', bad);
  try { state.extra[side] = await resizeToDataUrl(file); } catch (e) { return setError('err1', e.message); }
  invalidate();
  const img = slot.querySelector('.apreview');
  img.src = state.extra[side]; img.hidden = false;
  slot.querySelector('.aremove').hidden = false;
  slot.classList.add('filled');
}

function removeExtra(side, slot) {
  delete state.extra[side];
  const img = slot.querySelector('.apreview');
  img.hidden = true; img.removeAttribute('src');
  slot.querySelector('.aremove').hidden = true;
  slot.classList.remove('filled');
  invalidate();
}

// --- фото «Этапов взросления»
async function handleGrowthFile(i, file, slot) {
  setError('err1');
  const bad = checkFile(file);
  if (bad) return file && setError('err1', bad);
  let data;
  try { data = await resizeToDataUrl(file); } catch (e) { return setError('err1', e.message); }
  state.growth[i] = { data, name: file.name, label: slot.querySelector('.glabel').value };
  invalidate();
  const img = slot.querySelector('.gpreview');
  img.src = data; img.hidden = false;
  slot.querySelector('.gdrop').classList.add('has-photo');
  slot.querySelector('.gdrop').firstElementChild.textContent = '✓';
  slot.querySelector('.gremove').hidden = false;
  refreshLock();
}

function removeGrowth(i, slot) {
  state.growth[i] = undefined;
  const img = slot.querySelector('.gpreview');
  img.hidden = true; img.removeAttribute('src');
  slot.querySelector('.gdrop').classList.remove('has-photo');
  slot.querySelector('.gdrop').firstElementChild.textContent = '+';
  slot.querySelector('.gremove').hidden = true;
  invalidate();
  refreshLock();
}

// ---------- создание макета ----------
// Генерация идёт на сервере в фоне несколько минут: запускаем задачу и опрашиваем её статус.
function showProgress(step, pct) {
  $('loaderText').textContent = step;
  $('progressBar').style.width = Math.max(3, Math.min(100, pct)) + '%';
}

async function requestMockup(payload, onProgress) {
  const res = await fetch('/api/generate', {
    method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload),
  });
  const start = await res.json();
  if (!res.ok) throw new Error(start.error || 'Не удалось создать макет');

  const until = Date.now() + 25 * 60 * 1000;
  let failures = 0;
  while (Date.now() < until) {
    await new Promise((r) => setTimeout(r, 3000));
    let job;
    try {
      const r = await fetch('/api/generate/' + start.job, { cache: 'no-store' });
      job = await r.json();
      if (!r.ok) throw new Error(job.error || 'Не удалось получить статус');
      failures = 0;
    } catch (e) {
      // единичные сбои сети переживаем; настоящая ошибка (задача потеряна) — сразу наружу
      if (++failures >= 5 || /Задача не найдена/.test(e.message)) throw e;
      continue;
    }
    if (onProgress) onProgress(job.step, job.pct);
    if (job.status === 'done') return { id: start.id, demo: job.demo, views: job.views };
    if (job.status === 'error') throw new Error(job.error || 'Не удалось создать макет');
  }
  throw new Error('Создание макета заняло слишком много времени. Попробуйте ещё раз.');
}

function viewsHtml(views) {
  return `<div class="views">${Object.entries(VIEW_LABELS).map(([v, label]) =>
    `<figure><img src="${views[v]}" alt="${label}"><figcaption>${label}</figcaption></figure>`).join('')}</div>`;
}

async function generate() {
  setError('err2');
  if (!photosReady()) return setError('err2', isGrowth() ? `Загрузите все ${growthCount()} фото.` : 'Сначала загрузите фото.');
  $('gen').disabled = true;
  $('regen').disabled = true;
  $('loader').style.display = 'block';
  try {
    const results = [];
    showProgress('Отправляем фото', 2);
    if (isGrowth()) {
      // три фигурки создаются одновременно; полоса показывает среднюю готовность
      const n = growthCount();
      const pcts = new Array(n).fill(0);
      const jobs = state.growth.map((g, i) => requestMockup({
        photo: g.data, style: state.style, product: 'growth', label: g.label.trim(),
      }, (step, pct) => {
        pcts[i] = pct;
        showProgress(`Создаём ${n} фигурки: ${step.toLowerCase()}`, pcts.reduce((a, b) => a + b, 0) / n);
      }).then((r) => ({ label: g.label.trim() || `Фигурка ${i + 1}`, ...r })));
      results.push(...(await Promise.all(jobs)));
    } else {
      results.push({ label: '', ...(await requestMockup({
        photo: state.photo, style: state.style, people: state.people,
        extra: Object.fromEntries(Object.entries(state.extra).filter(([, v]) => v)),
      }, showProgress)) });
    }
    state.generationIds = results.map((r) => r.id);
    $('results').innerHTML = results.map((r) =>
      (r.label ? `<h3 class="sub">${r.label.replace(/</g, '&lt;')}</h3>` : '') + viewsHtml(r.views)).join('');
    $('demoNotice').style.display = results[0].demo ? 'block' : 'none';
    $('p3').style.display = 'block';
    $('gen').textContent = 'Показать макет заново';
    $('p3').scrollIntoView({ behavior: 'smooth', block: 'start' });
  } catch (e) {
    setError('err2', e.message);
  } finally {
    $('gen').disabled = false;
    $('regen').disabled = false;
    $('loader').style.display = 'none';
  }
}
$('gen').addEventListener('click', generate);
$('regen').addEventListener('click', generate);

$('toOrder').addEventListener('click', () => {
  $('p4').style.display = 'block';
  $('p4').scrollIntoView({ behavior: 'smooth', block: 'start' });
});

// ---------- адрес и доставка ----------
const addrInput = $('address');
const list = $('suggest');
let timer = null, seq = 0, items = [];

function resetDelivery() {
  state.delivery = null;
  $('deliveryBox').hidden = true;
  $('deliveryOpts').innerHTML = '';
  updateTotal();
}

function hideList() { list.hidden = true; list.innerHTML = ''; }

addrInput.addEventListener('input', () => {
  state.address = null;
  resetDelivery();
  $('addrHint').textContent = '';
  $('addrHint').className = 'hint';
  clearTimeout(timer);
  if (state.config && state.config.addressDemo) return;
  const q = addrInput.value.trim();
  if (q.length < 3) return hideList();
  timer = setTimeout(async () => {
    const my = ++seq;
    try {
      const r = await fetch('/api/address/suggest?q=' + encodeURIComponent(q));
      const data = await r.json();
      if (my !== seq) return;
      items = data.items || [];
      list.innerHTML = '';
      items.forEach((it, i) => {
        const li = document.createElement('li');
        li.textContent = it.value;
        li.addEventListener('mousedown', (e) => { e.preventDefault(); pick(i); });
        li.addEventListener('touchend', (e) => { e.preventDefault(); pick(i); });
        list.appendChild(li);
      });
      list.hidden = !items.length;
      if (!items.length) $('addrHint').textContent = 'Такой адрес не найден — проверьте написание.';
    } catch (e) { hideList(); }
  }, 300);
});
addrInput.addEventListener('blur', () => setTimeout(hideList, 200));

function pick(i) {
  const it = items[i];
  hideList();
  addrInput.value = it.value;
  if (!it.house) {   // дошли только до улицы/города — просим продолжить до номера дома
    addrInput.value = it.value + ', ';
    addrInput.focus();
    addrInput.dispatchEvent(new Event('input'));
    $('addrHint').textContent = 'Уточните номер дома.';
    return;
  }
  state.address = it;
  $('addrHint').textContent = 'Адрес найден ✓';
  $('addrHint').className = 'hint ok';
  quoteDelivery();
}

function currentAddress() {
  if (state.config && state.config.addressDemo) {
    return { value: addrInput.value.trim(), city: $('demoCity').value.trim(), postcode: $('demoPost').value.trim() };
  }
  return state.address;
}

async function quoteDelivery() {
  const address = currentAddress();
  if (!address) return;
  $('deliveryBox').hidden = false;
  $('deliveryOpts').innerHTML = '<div class="hint">Считаем доставку…</div>';
  try {
    const r = await fetch('/api/delivery/quote', {
      method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ address }),
    });
    const data = await r.json();
    if (!r.ok) throw new Error(data.error);
    $('deliveryOpts').innerHTML = '';
    data.options.forEach((o, i) => {
      const l = document.createElement('label');
      l.className = 'opt';
      l.innerHTML = `<input type="radio" name="delivery" value="${o.id}"><span>${o.name}${o.days ? `<br><small class="muted">срок: ${o.days}</small>` : ''}</span><b>${fmt(o.price)}</b>`;
      l.querySelector('input').addEventListener('change', () => { state.delivery = o; updateTotal(); });
      $('deliveryOpts').appendChild(l);
      if (i === 0) { l.querySelector('input').checked = true; state.delivery = o; }
    });
    updateTotal();
  } catch (e) {
    $('deliveryOpts').innerHTML = `<div class="err">${e.message || 'Не удалось рассчитать доставку'}</div>`;
  }
}
['demoCity', 'demoPost'].forEach((id) => $(id).addEventListener('change', () => { resetDelivery(); if (currentAddress().city && currentAddress().postcode.length === 6) quoteDelivery(); }));
addrInput.addEventListener('change', () => { if (state.config && state.config.addressDemo) { resetDelivery(); const a = currentAddress(); if (a.value && a.city && a.postcode.length === 6) quoteDelivery(); } });

// ---------- заказ ----------
$('pay').addEventListener('click', async () => {
  setError('err4');
  const body = Object.fromEntries(new FormData($('orderForm')).entries());
  body.consentOffer = $('consentOffer').checked;
  body.consentPd = $('consentPd').checked;
  body.generationIds = state.generationIds;
  body.size = '10-15';
  body.address = currentAddress();
  body.deliveryId = state.delivery ? state.delivery.id : '';
  if (!state.generationIds.length) return setError('err4', 'Сначала создайте макет.');
  if (!body.address || !body.address.value) return setError('err4', 'Введите адрес и выберите его из списка подсказок.');
  if (!state.delivery) return setError('err4', 'Выберите способ доставки.');
  $('pay').disabled = true;
  try {
    const res = await fetch('/api/orders', {
      method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body),
    });
    const data = await res.json();
    if (!res.ok) throw new Error(data.error || 'Не удалось оформить заказ');
    location.href = data.confirmationUrl;
  } catch (e) {
    setError('err4', e.message);
    $('pay').disabled = false;
  }
});
