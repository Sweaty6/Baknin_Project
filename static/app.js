'use strict';

(() => {
  const $ = (selector, root = document) => root.querySelector(selector);
  const siteBase = $('meta[name="baknin-base"]')?.content || '/';
  const siteUrl = href => href.startsWith('/') && !href.startsWith('//') ? siteBase + href.slice(1) : href;
  const sentimentLabels = {positive: 'Положительная', negative: 'Отрицательная', neutral: 'Нейтральная', mixed: 'Смешанная'};
  const reliabilityLabels = {insufficient: 'Недостаточно данных', limited: 'Ограниченная выборка', moderate: 'Умеренная выборка', high: 'Большая выборка'};
  const aspectLabels = {education: 'Качество обучения', teaching: 'Преподавание и учебный процесс', teachers: 'Преподаватели', career: 'Практика и трудоустройство', dormitory: 'Общежитие', accommodation: 'Общежитие', practice: 'Практика', employment: 'Трудоустройство', infrastructure: 'Инфраструктура', administration: 'Администрация', student_life: 'Студенческая жизнь', admission: 'Поступление', cost: 'Стоимость обучения', finance: 'Стоимость и финансы', scholarships: 'Стипендии', food: 'Питание', research: 'Наука'};
  const integer = value => Number.isFinite(Number(value)) ? new Intl.NumberFormat('ru-RU').format(Number(value)) : '—';
  const percent = value => value !== null && value !== undefined && Number.isFinite(Number(value)) ? `${new Intl.NumberFormat('ru-RU', {maximumFractionDigits: 1}).format(Number(value))}%` : '—';
  const score = value => value !== null && value !== undefined && Number.isFinite(Number(value)) ? `${new Intl.NumberFormat('ru-RU', {maximumFractionDigits: 1}).format(Number(value))} / 100` : '—';
  const name = item => item.short_name || item.full_name || item.name || 'Университет';
  const aspectName = aspect => typeof aspect === 'string' ? (aspectLabels[aspect] || aspect) : (aspect.label || aspectLabels[aspect.id || aspect.aspect] || aspect.id || aspect.aspect || 'Тема');

  function node(tag, className, text) {
    const element = document.createElement(tag);
    if (className) element.className = className;
    if (text !== undefined && text !== null) element.textContent = String(text);
    return element;
  }

  function append(parent, ...children) {
    for (const child of children) if (child) parent.append(child);
    return parent;
  }

  function safeUrl(value) {
    if (typeof value !== 'string' || !value) return null;
    try {
      const url = new URL(value);
      return url.protocol === 'https:' && !url.username && !url.password ? url.href : null;
    } catch { return null; }
  }

  function link(text, href, className, external = false) {
    const a = node('a', className, text);
    if (external) {
      const url = safeUrl(href);
      if (!url) return node('span', className, text);
      a.href = url;
      a.target = '_blank';
      a.rel = 'noopener noreferrer';
      a.setAttribute('aria-label', `${text} (откроется в новой вкладке)`);
    } else a.href = siteUrl(href);
    return a;
  }

  function date(value) {
    if (!value) return 'Дата не указана';
    const parsed = new Date(value);
    return Number.isNaN(parsed.getTime()) ? 'Дата не указана' : new Intl.DateTimeFormat('ru-RU', {day: 'numeric', month: 'short', year: 'numeric', timeZone: 'Europe/Moscow'}).format(parsed);
  }

  async function api(path, options = {}) {
    if (globalThis.BakninPagesAPI) return globalThis.BakninPagesAPI.request(path, options);
    const response = await fetch(path, {...options, headers: {'Accept': 'application/json', ...options.headers}});
    const data = await response.json().catch(() => null);
    if (!response.ok) throw new Error(typeof data?.error === 'string' ? data.error : 'Не удалось получить данные. Попробуйте ещё раз.');
    if (!data) throw new Error('Сервер вернул неожиданный ответ.');
    return data;
  }

  function empty(title, description, action) {
    const state = node('div', 'empty-state');
    append(state, node('span', 'empty-icon', '→'), node('h2', '', title), node('p', '', description));
    if (action) state.append(action);
    return state;
  }

  function fail(container, error, retry) {
    container.replaceChildren();
    container.setAttribute('aria-busy', 'false');
    const state = node('div', 'error-state');
    append(state, node('h2', '', 'Не удалось загрузить данные'), node('p', '', error.message));
    if (retry) {
      const button = node('button', 'button button-secondary', 'Попробовать снова');
      button.type = 'button';
      button.addEventListener('click', retry);
      state.append(button);
    }
    container.append(state);
  }

  function badge(sentiment) {
    const key = Object.hasOwn(sentimentLabels, sentiment) ? sentiment : 'neutral';
    return node('span', `sentiment-badge ${key}`, sentimentLabels[key]);
  }

  function fillSelect(select, items, getValue, getLabel) {
    if (!select) return;
    const value = select.value;
    for (const option of Array.from(select.options).slice(1)) option.remove();
    for (const item of items || []) {
      const option = node('option', '', getLabel(item));
      option.value = String(getValue(item));
      select.append(option);
    }
    if (Array.from(select.options).some(option => option.value === value)) select.value = value;
  }

  function params(form) {
    const search = new URLSearchParams();
    for (const [key, value] of new FormData(form)) if (String(value).trim()) search.set(key, String(value).trim());
    return search;
  }

  function applyUrlParams(form) {
    const search = new URLSearchParams(location.search);
    for (const input of form.elements) if (input.name && search.has(input.name) && input.tagName !== 'SELECT') input.value = search.get(input.name);
    return search;
  }

  function validDateRange(form) {
    const from = form.elements.namedItem('date_from');
    const to = form.elements.namedItem('date_to');
    if (to) to.setCustomValidity(from?.value && to.value && from.value > to.value ? 'Дата окончания должна быть не раньше даты начала.' : '');
    return form.reportValidity();
  }

  function universityCard(item) {
    const card = node('article', 'university-card');
    const heading = node('div', 'card-top');
    const initials = (item.short_name || item.full_name || 'ВУ').replace(/[^А-Яа-яA-Za-z]/g, '').slice(0, 2).toUpperCase();
    append(heading, node('span', 'university-initials', initials), node('span', `rank-tag ${item.rank == null ? 'small-sample' : ''}`, item.rank == null ? 'Рейтинг не рассчитан' : `№ ${item.rank} в выборке`));
    const title = node('h3');
    title.append(link(name(item), `/universities/${encodeURIComponent(item.id)}`));
    append(card, heading, title, node('p', 'card-location', [item.city, item.region].filter(Boolean).join(' · ') || 'Регион не указан'));
    if (item.short_name && item.full_name && item.short_name !== item.full_name) card.append(node('p', 'card-full-name', item.full_name));
    const stats = node('div', 'card-stats');
    const positive = node('div');
    append(positive, node('strong', '', percent(item.positive_percent)), node('span', '', 'положительных отзывов'));
    const reviews = node('div', 'card-reviews');
    append(reviews, node('strong', '', integer(item.reviews_total ?? item.review_count ?? 0)), node('span', '', 'отзывов в базе'));
    append(stats, positive, reviews);
    const footer = node('div', 'card-footer');
    append(footer, link('Подробнее →', `/universities/${encodeURIComponent(item.id)}`), node('span', 'reliability', reliabilityLabels[item.reliability] || (item.small_sample ? 'Недостаточно данных' : '')));
    append(card, stats, footer);
    return card;
  }

  async function initHome() {
    const form = $('#university-filters');
    const list = $('#university-list');
    const initial = applyUrlParams(form);
    let regionInitialized = false;
    let requestVersion = 0;
    const load = async () => {
      if (!validDateRange(form)) return;
      const version = ++requestVersion;
      list.setAttribute('aria-busy', 'true');
      try {
        const search = params(form);
        const data = await api(`/api/universities?${search}`);
        if (version !== requestVersion) return;
        fillSelect($('#region'), data.regions, item => typeof item === 'string' ? item : item.id || item.name, item => typeof item === 'string' ? item : item.name || item.label);
        if (!regionInitialized && initial.has('region')) {
          $('#region').value = initial.get('region');
          regionInitialized = true;
          if ($('#region').value && !search.has('region')) return load();
        }
        regionInitialized = true;
        const items = data.items || [];
        $('#university-count').textContent = `Найдено университетов: ${integer(items.length)}`;
        const publicationNote = $('.results-top > span');
        if (publicationNote && data.meta?.exported_at) publicationNote.textContent = `Публикация данных: ${date(data.meta.exported_at)}`;
        list.replaceChildren(...items.map(universityCard));
        if (!items.length) list.append(empty('Университеты не найдены', 'Попробуйте изменить название, регион или период отзывов.'));
        list.setAttribute('aria-busy', 'false');
        const saved = params(form);
        history.replaceState({}, '', `${location.pathname}${saved.size ? `?${saved}` : ''}${location.hash}`);
      } catch (error) {
        if (version !== requestVersion) return;
        $('#university-count').textContent = 'Данные недоступны';
        fail(list, error, load);
      }
    };
    form.addEventListener('submit', event => {event.preventDefault(); load();});
    form.addEventListener('input', () => { const to = form.elements.namedItem('date_to'); if (to) to.setCustomValidity(''); });
    await load();
  }

  function reviewCard(review) {
    const card = node('article', 'review-card');
    const heading = node('div', 'review-header');
    const identity = node('div');
    const university = review.university || {};
    const universityId = review.university_id || university.id;
    const universityName = review.university_short_name || review.short_name || university.short_name || review.university_name || university.full_name || review.full_name || 'Отзыв об университете';
    append(identity, universityId ? link(universityName, `/universities/${encodeURIComponent(universityId)}`, 'review-university') : node('span', 'review-university', universityName), node('span', 'review-date', date(review.published_at)));
    append(heading, identity, badge(review.sentiment));
    card.append(heading);
    if (review.text && review.display_allowed !== false) card.append(node('p', 'review-text', review.text));
    else card.append(node('p', 'review-withheld', 'Полный текст не публикуется: разрешение на показ не подтверждено. Прочитайте отзыв на исходной площадке.'));
    const aspects = node('div', 'topic-chips');
    for (const aspect of review.aspects || []) {
      const pending = aspect.approved === false || aspect.approved === 0;
      const tag = node('span', 'tag', `${aspectName(aspect)} · ${sentimentLabels[aspect.sentiment] || 'Тональность не указана'}${pending ? ' · тема не проверена' : ''}`);
      if (pending) tag.classList.add('tag-pending');
      aspects.append(tag);
    }
    if (aspects.childElementCount) card.append(aspects);
    if (review.needs_review) card.append(node('span', 'review-check', 'Тональность требует проверки человеком'));
    else if (review.manual_reviewed || review.label_origin === 'manual') card.append(node('span', 'review-confirmed', 'Тональность проверена человеком'));
    const source = review.source || {};
    const footer = node('div', 'review-meta');
    const sourceUrl = review.source_url || source.url;
    append(footer, node('span', '', source.platform || review.platform || 'Источник не указан'), safeUrl(sourceUrl) ? link('Читать источник →', sourceUrl, '', true) : node('span', '', 'Ссылка недоступна'));
    card.append(footer);
    return card;
  }

  async function initReviews() {
    const form = $('#review-filters');
    const list = $('#review-list');
    const pagination = $('#review-pagination');
    const initial = applyUrlParams(form);
    let currentPage = Math.max(1, Number.parseInt(initial.get('page') || '1', 10) || 1);
    let requestVersion = 0;
    const load = async (page = 1) => {
      if (!validDateRange(form)) return;
      const version = ++requestVersion;
      currentPage = page;
      list.setAttribute('aria-busy', 'true');
      try {
        const search = params(form);
        if (page > 1) search.set('page', String(page));
        const data = await api(`/api/reviews?${search}`);
        if (version !== requestVersion) return;
        $('#review-count').textContent = `Найдено отзывов: ${integer(data.total ?? 0)}`;
        list.replaceChildren(...(data.items || []).map(reviewCard));
        if (!(data.items || []).length) list.append(empty('Пока нет отзывов', 'Для выбранных условий нет подтверждённых записей. Попробуйте изменить фильтры. Если база пуста, сначала нужно подключить разрешённые источники данных.', link('О данных и источниках →', '/methodology#sources', 'text-link')));
        pagination.replaceChildren();
        if (data.pages > 1) {
          const previous = node('button', 'button button-secondary', '← Назад');
          const next = node('button', 'button button-secondary', 'Далее →');
          previous.type = next.type = 'button';
          previous.disabled = data.page <= 1;
          next.disabled = data.page >= data.pages;
          previous.addEventListener('click', () => {load(currentPage - 1); list.scrollIntoView({block: 'start'});});
          next.addEventListener('click', () => {load(currentPage + 1); list.scrollIntoView({block: 'start'});});
          append(pagination, previous, node('span', '', `Страница ${data.page} из ${data.pages}`), next);
        }
        list.setAttribute('aria-busy', 'false');
        history.replaceState({}, '', `${location.pathname}${search.size ? `?${search}` : ''}`);
      } catch (error) {
        if (version !== requestVersion) return;
        $('#review-count').textContent = 'Данные недоступны';
        fail(list, error, () => load(currentPage));
      }
    };
    form.addEventListener('submit', event => {event.preventDefault(); load();});
    form.addEventListener('input', () => { const to = form.elements.namedItem('date_to'); if (to) to.setCustomValidity(''); });
    const auxiliary = await Promise.allSettled([api('/api/universities'), api('/api/methodology')]);
    if (auxiliary[0].status === 'fulfilled') fillSelect($('#review-university'), auxiliary[0].value.items, item => item.id, name);
    if (auxiliary[1].status === 'fulfilled') {
      const method = auxiliary[1].value;
      fillSelect($('#review-aspect'), method.aspects || method.aspect_categories || [], item => typeof item === 'string' ? item : item.id || item.aspect, aspectName);
      fillSelect($('#review-source'), method.sources || [], item => item.id, item => item.platform || item.name || item.url);
    }
    for (const select of form.querySelectorAll('select')) if (initial.has(select.name)) {
      const value = initial.get(select.name);
      if (value && !Array.from(select.options).some(option => option.value === value)) {
        const option = node('option', '', `${select.name === 'source_id' ? 'Источник' : select.name === 'aspect' ? 'Тема' : 'Университет'}: ${value}`);
        option.value = value;
        select.append(option);
      }
      select.value = value;
    }
    await load(currentPage);
  }

  function isRules(data) {
    return /rule|lexicon|dictionary|словар/i.test(String(data.engine || data.model_type || '')) || (!data.engine && data.confidence == null);
  }

  function renderAnalysis(data, container) {
    container.replaceChildren();
    const rules = isRules(data);
    const top = node('div', 'analysis-result-heading');
    append(top, node('h2', '', 'Результат анализа'), node('span', `analysis-engine ${rules ? 'warning' : ''}`, rules ? 'Словарный анализ • требует проверки' : `Модель: ${data.model_version || data.engine || 'версия не указана'}`));
    const sentiment = data.mixed ? 'mixed' : data.sentiment;
    append(container, top, node('h3', 'result-sentiment', sentimentLabels[sentiment] || 'Тональность не определена'));
    if (!rules && data.confidence != null && Number.isFinite(Number(data.confidence))) container.append(node('p', 'result-summary', `Уверенность модели: ${percent(Number(data.confidence) <= 1 ? Number(data.confidence) * 100 : data.confidence)}. Это оценка модели, а не гарантия правильности.`));
    else container.append(node('p', 'result-summary', 'Оценка по языковым признакам. Подтверждённая вероятность правильного ответа не рассчитана.'));
    const explanation = data.explanation;
    if (explanation) {
      const text = typeof explanation === 'string' ? explanation : Array.isArray(explanation) ? explanation.join(' ') : explanation.summary || explanation.description || explanation.reason;
      if (text) container.append(node('p', 'result-explanation', text));
    }
    const aspectSection = node('section', 'result-section');
    aspectSection.append(node('h3', '', 'Темы в отзыве'));
    for (const aspect of data.aspects || []) {
      const item = node('div', 'aspect-item');
      const heading = node('div', 'aspect-heading');
      append(heading, node('span', '', aspectName(aspect)), badge(aspect.sentiment));
      item.append(heading);
      const evidence = aspect.evidence || aspect.context;
      if (evidence) {
        const text = Array.isArray(evidence) ? evidence.map(row => typeof row === 'string' ? row : row.text || row.context || '').filter(Boolean).join(' · ') : String(evidence);
        if (text) item.append(node('p', 'aspect-evidence', text));
      }
      aspectSection.append(item);
    }
    if (!(data.aspects || []).length) aspectSection.append(node('p', 'text-small muted no-margin', 'Явные упоминания тем не обнаружены.'));
    container.append(aspectSection);
    if (Array.isArray(data.keywords) && data.keywords.length) {
      const keywords = node('section', 'result-section');
      const chips = node('div', 'topic-chips');
      keywords.append(node('h3', '', 'Ключевые слова'));
      for (const word of data.keywords) chips.append(node('span', 'tag', typeof word === 'string' ? word : word.word || word.text || word.keyword || ''));
      keywords.append(chips);
      container.append(keywords);
    }
    if (rules || data.needs_review || data.mixed) container.append(node('p', 'notice', data.mixed ? 'В тексте есть разные впечатления. Проверьте тональность отдельных тем и прочитайте отзыв целиком.' : 'Результат требует проверки человеком. Особенно осторожно следует трактовать сарказм, отрицания и короткие тексты.'));
  }

  function initAnalysis() {
    const form = $('#analysis-form');
    const textarea = $('#analysis-text');
    const result = $('#analysis-result');
    const initial = result.cloneNode(true);
    const submit = $('#analysis-submit');
    let activeRequest = 0;
    textarea.addEventListener('input', () => {$('#text-counter').textContent = `${integer(textarea.value.length)} / 5 000`; textarea.setCustomValidity('');});
    form.addEventListener('submit', async event => {
      event.preventDefault();
      const text = textarea.value.trim();
      textarea.setCustomValidity(text ? '' : 'Введите текст отзыва.');
      if (!form.reportValidity()) return;
      const request = ++activeRequest;
      submit.disabled = true;
      submit.textContent = 'Анализируем…';
      result.setAttribute('aria-busy', 'true');
      try {
        const data = await api('/predict', {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({text})});
        if (request === activeRequest) renderAnalysis(data, result);
      } catch (error) {
        if (request === activeRequest) result.replaceChildren(node('p', 'inline-error', error.message));
      } finally {
        if (request === activeRequest) {
          submit.disabled = false;
          submit.textContent = 'Проанализировать →';
          result.setAttribute('aria-busy', 'false');
        }
      }
    });
    form.addEventListener('reset', () => {
      activeRequest++;
      $('#text-counter').textContent = '0 / 5 000';
      textarea.setCustomValidity('');
      submit.disabled = false;
      submit.textContent = 'Проанализировать →';
      result.replaceChildren(...Array.from(initial.childNodes).map(child => child.cloneNode(true)));
      result.setAttribute('aria-busy', 'false');
    });
  }

  function metric(value, label, note, highlight = false) {
    const card = node('div', `metric ${highlight ? 'highlight' : ''}`);
    append(card, node('strong', '', value), node('span', 'metric-label', label));
    if (note) card.append(node('span', 'metric-note', note));
    return card;
  }

  function detailSection(id, title, action) {
    const section = node('section', 'detail-section');
    section.id = id;
    const heading = node('div', 'detail-section-heading');
    append(heading, node('h2', '', title), action);
    section.append(heading);
    return section;
  }

  async function initUniversity() {
    const container = $('#university-detail');
    const id = document.body.dataset.universityId || location.pathname.split('/').filter(Boolean).at(-1);
    const load = async () => {
      container.setAttribute('aria-busy', 'true');
      try {
        const data = await api(`/api/universities/${encodeURIComponent(id)}`);
        const university = data.university || data;
        document.title = `${name(university)} — АГРО / ОТЗЫВЫ`;
        container.replaceChildren();
        const heading = node('div', 'detail-heading');
        const copy = node('div');
        append(copy, node('span', 'eyebrow', 'АГРАРНЫЙ УНИВЕРСИТЕТ'), node('h1', '', university.full_name || name(university)), node('p', '', [university.city, university.region].filter(Boolean).join(' · ')));
        if (university.short_name) copy.append(node('p', '', university.short_name));
        append(heading, copy, safeUrl(university.official_url) ? link('Официальный сайт →', university.official_url, 'text-link official-link', true) : null);
        const navigation = node('nav', 'detail-quick-nav');
        navigation.setAttribute('aria-label', 'Разделы университета');
        append(navigation, link('Обзор', '#overview'), link('Темы отзывов', '#topics'), link('Динамика', '#dynamics'), link('Отзывы', '#recent-reviews'), link('Источники', '#sources'));
        const stats = node('div', 'detail-metrics');
        stats.id = 'overview';
        append(stats,
          metric(score(university.smoothed_score ?? university.score), 'Сглаженная оценка', university.smoothed_score == null && university.score == null ? 'Недостаточно проверенных данных' : 'С учётом среднего по корпусу', true),
          metric(percent(university.positive_percent), 'Положительных отзывов', university.positive_percent == null ? 'Проверенной выборки пока нет' : 'Наблюдаемая доля в выборке'),
          metric(integer(university.reviews_total ?? university.review_count ?? 0), 'Отзывов в базе', `${integer(university.eligible_reviews ?? 0)} допущено к расчёту`),
          metric(university.rank == null ? '—' : `№ ${integer(university.rank)}`, 'Место в текущей выборке', reliabilityLabels[university.reliability] || 'Достаточность данных не установлена')
        );
        append(container, heading, navigation, stats);
        if (university.small_sample || university.rank == null) container.append(node('p', 'notice', 'Проверенных отзывов недостаточно для сравнения. Наблюдаемая доля положительных отзывов не подтверждает устойчивый результат; место и сглаженная оценка появятся после достижения порога, описанного в методологии.'));
        const distribution = detailSection('distribution', 'Тональность проверенных отзывов');
        if (university.eligible_reviews > 0) {
          const chart = node('div', 'distribution-grid');
          for (const sentiment of ['positive', 'negative', 'neutral']) {
            const item = node('div', `distribution-item ${sentiment}`);
            const count = Number(university[sentiment] || 0);
            const progress = node('progress');
            progress.max = university.eligible_reviews;
            progress.value = count;
            progress.setAttribute('aria-label', `${sentimentLabels[sentiment]} тональность: ${integer(count)} из ${integer(university.eligible_reviews)}`);
            append(item, node('span', 'distribution-label', sentimentLabels[sentiment]), node('strong', '', integer(count)), progress, node('span', 'distribution-percent', percent(100 * count / university.eligible_reviews)));
            chart.append(item);
          }
          distribution.append(chart);
          distribution.append(node('p', 'text-small muted', `Не включено в расчёт: ${integer(university.pending_reviews ?? Math.max(0, (university.reviews_total || 0) - university.eligible_reviews))}. Эти записи требуют проверки или не имеют определённой тональности.`));
        } else distribution.append(empty('Проверенной выборки пока нет', 'Распределение появится после проверки тональности реальных отзывов.'));
        const signals = detailSection('signals', 'Сильные стороны и сложности');
        const signalsGrid = node('div', 'signals-grid');
        for (const [key, label, tone] of [['strengths', 'Что хвалят', 'positive'], ['weaknesses', 'Что критикуют', 'negative']]) {
          const panel = node('div', 'signal-panel');
          panel.append(node('h3', '', label));
          const entries = university[key] || [];
          for (const entry of entries) {
            const item = node('div', 'signal-item');
            const top = node('div', 'aspect-heading');
            append(top, node('span', '', aspectName(entry)), node('span', `sentiment-badge ${tone}`, `${integer(entry[tone] || 0)} подтверждений`));
            item.append(top);
            const evidence = (entry.evidence || []).find(review => review.sentiment === tone);
            if (evidence?.text) item.append(node('p', 'aspect-evidence', evidence.text));
            if (safeUrl(evidence?.source_url)) item.append(link('Источник цитаты →', evidence.source_url, 'text-link', true));
            panel.append(item);
          }
          if (!entries.length) panel.append(node('p', 'text-small muted no-margin', 'Пока недостаточно подтверждённых упоминаний. Это не означает отсутствия сильных сторон или сложностей.'));
          signalsGrid.append(panel);
        }
        signals.append(signalsGrid);
        const topics = detailSection('topics', 'О чём говорят студенты');
        const topicGrid = node('div', 'topic-grid');
        for (const topic of data.topics || []) {
          const card = node('article', 'topic-card');
          const counts = node('div', 'topic-counts');
          append(counts, node('span', 'positive-count', `+ ${integer(topic.positive || 0)}`), node('span', 'negative-count', `− ${integer(topic.negative || 0)}`), node('span', '', `Нейтрально: ${integer(topic.neutral || 0)}`));
          append(card, node('h3', '', aspectName(topic)), counts);
          if (Array.isArray(topic.evidence) && topic.evidence.length) {
            const evidence = topic.evidence[0];
            if (evidence.text) card.append(node('p', 'aspect-evidence', evidence.text));
            if (safeUrl(evidence.source_url)) card.append(link('Источник цитаты →', evidence.source_url, 'text-link', true));
          }
          topicGrid.append(card);
        }
        topics.append(topicGrid.childElementCount ? topicGrid : empty('Темы пока не определены', 'Анализ тем появится после добавления отзывов с разрешённых источников.'));
        const dynamics = detailSection('dynamics', 'Отзывы во времени');
        if (data.dynamics?.length) {
          const wrap = node('div', 'table-wrap');
          const table = node('table');
          const caption = node('caption', 'visually-hidden', 'Динамика отзывов по месяцам');
          const thead = node('thead');
          const row = node('tr');
          for (const label of ['Месяц', 'Всего', 'Положительные', 'Отрицательные', 'Нейтральные', 'На проверку']) {const th = node('th', '', label); th.scope = 'col'; row.append(th);}
          thead.append(row);
          const tbody = node('tbody');
          for (const month of data.dynamics) {
            const tr = node('tr');
            for (const value of [month.month, integer(month.total || 0), integer(month.positive || 0), integer(month.negative || 0), integer(month.neutral || 0), integer(month.needs_review || 0)]) tr.append(node('td', '', value));
            tbody.append(tr);
          }
          append(table, caption, thead, tbody);
          wrap.append(table);
          dynamics.append(wrap);
        } else dynamics.append(empty('История ещё не сформирована', 'Для отображения динамики нужны отзывы с датами публикации.'));
        const reviews = detailSection('recent-reviews', 'Последние отзывы', link('Все отзывы →', `/reviews?university_id=${encodeURIComponent(id)}`, 'text-link'));
        const reviewGrid = node('div', 'review-grid');
        for (const review of data.recent_reviews || []) reviewGrid.append(reviewCard({...review, university, university_id: id}));
        reviews.append(reviewGrid.childElementCount ? reviewGrid : empty('Пока нет отзывов', 'В базе ещё нет подтверждённых отзывов об этом университете. Случайные примеры не используются для рейтинга.'));
        const sources = detailSection('sources', 'Источники данных', link('Правила работы с данными →', '/methodology#sources', 'text-link'));
        const sourceList = node('div', 'source-list');
        for (const source of data.sources || []) {
          const card = node('article', 'source-card');
          const sourceCopy = node('div');
          append(sourceCopy, node('h3', '', source.platform || 'Источник'), node('p', '', `Отзывов: ${integer(source.review_count || 0)}`));
          if (source.terms_checked_at) sourceCopy.append(node('p', '', `Условия проверены: ${date(source.terms_checked_at)}`));
          append(card, sourceCopy, safeUrl(source.url) ? link('Открыть →', source.url, '', true) : null);
          sourceList.append(card);
        }
        sources.append(sourceList.childElementCount ? sourceList : empty('Источники ещё не подключены', 'Для сбора и показа отзывов необходимо проверить условия использования каждой площадки.'));
        append(container, distribution, signals, topics, dynamics, reviews, sources);
        container.setAttribute('aria-busy', 'false');
      } catch (error) { fail(container, error, load); }
    };
    await load();
  }

  async function initMethodology() {
    const info = $('#engine-info');
    try {
      const data = await api('/api/methodology');
      const analyzer = data.analyzer || data.model || data;
      const rules = isRules(analyzer);
      info.replaceChildren(node('strong', '', rules ? 'Словарный анализ • требует проверки' : `Режим анализа: ${analyzer.engine || analyzer.model_version || 'модель'}`));
      info.append(node('p', '', rules ? 'Для текущего словарного режима точность на независимой выборке не подтверждена. Вместо вероятности показываются признаки и объяснение результата.' : `Версия: ${analyzer.model_version || analyzer.version || 'не указана'}. Показатели качества доступны только при наличии независимой проверки.`));
      if (data.exported_at) info.append(node('p', '', `Публикация данных: ${date(data.exported_at)}.`));
      if (data.timezone) info.append(node('p', '', 'Даты отзывов, фильтры периода и месяцы динамики используют время Москвы.'));
      const aspects = data.aspects || data.aspect_categories || [];
      const chips = $('#method-aspects');
      chips.replaceChildren();
      for (const aspect of aspects) chips.append(node('span', 'tag', aspectName(aspect)));
      if (!aspects.length) chips.append(node('p', 'text-small muted no-margin', 'Набор тем для текущего анализатора не опубликован.'));
      const ranking = data.ranking || data.rating || {};
      const rankingInfo = $('#ranking-info');
      rankingInfo.replaceChildren(node('strong', '', 'Правила текущего расчёта'));
      const threshold = ranking.min_reviews ?? ranking.min_sample ?? ranking.minimum_reviews ?? data.min_reviews;
      if (threshold != null) rankingInfo.append(node('p', '', `Минимум проверенных отзывов для включения в рейтинг: ${integer(threshold)}.`));
      const formula = ranking.formula || ranking.description || data.ranking_formula || data.score_formula;
      if (formula) rankingInfo.append(node('p', '', typeof formula === 'string' ? formula : 'Подробные параметры расчёта доступны в API методологии.'));
      const smoothing = ranking.prior_weight ?? ranking.smoothing_strength ?? ranking.prior_strength ?? data.smoothing;
      if (smoothing != null) rankingInfo.append(node('p', '', `Сглаживание учитывает среднюю тональность всей выборки; вес предварительной оценки: ${integer(smoothing)}.`));
      if (data.score_formula) rankingInfo.append(node('p', '', 'P — положительные, N — отрицательные, U — нейтральные проверенные отзывы. p₀ — доля положительных во всём проверенном корпусе до применения фильтров.'));
      if (data.positive_percent_formula) rankingInfo.append(node('p', '', `Наблюдаемая доля положительных отзывов: ${data.positive_percent_formula}.`));
      if (data.parameter_status) rankingInfo.append(node('p', '', data.parameter_status));
      if (rankingInfo.childElementCount === 1) rankingInfo.append(node('p', '', 'Процент положительных отзывов считается по пригодной для расчёта выборке. Точные параметры текущего рейтинга не опубликованы.'));
      const validationInfo = $('#model-validation');
      const report = data.metrics_available && data.metrics ? data.metrics : null;
      if (report?.test?.macro_f1 != null) {
        validationInfo.replaceChildren(node('p', '', `Опубликована оценка на отложенной выборке: test macro-F1 ${new Intl.NumberFormat('ru-RU', {maximumFractionDigits: 3}).format(report.test.macro_f1)}${report.dataset?.test?.size != null ? `, ${integer(report.dataset.test.size)} текстов` : ''}. Версия: ${report.metadata?.model_version || analyzer.model_version || 'не указана'}.`));
        if (report.dummy_baseline_test?.macro_f1 != null) validationInfo.append(node('p', '', `Базовый классификатор (наиболее частый класс): macro-F1 ${new Intl.NumberFormat('ru-RU', {maximumFractionDigits: 3}).format(report.dummy_baseline_test.macro_f1)}.`));
        const matrix = report.test.confusion_matrix;
        const labels = report.test.confusion_matrix_labels || ['positive', 'negative', 'neutral'];
        if (Array.isArray(matrix) && matrix.length) {
          const wrap = node('div', 'table-wrap');
          const table = node('table');
          const caption = node('caption', 'text-small', 'Матрица ошибок: строки — реальные классы, столбцы — прогноз');
          const thead = node('thead');
          const header = node('tr');
          const corner = node('th', '', 'Реальный класс'); corner.scope = 'col'; header.append(corner);
          for (const label of labels) {const th = node('th', '', sentimentLabels[label] || label); th.scope = 'col'; header.append(th);}
          thead.append(header);
          const tbody = node('tbody');
          matrix.forEach((values, index) => {
            const row = node('tr');
            const label = node('th', '', sentimentLabels[labels[index]] || labels[index]); label.scope = 'row'; row.append(label);
            for (const value of values) row.append(node('td', '', integer(value)));
            tbody.append(row);
          });
          append(table, caption, thead, tbody); wrap.append(table); validationInfo.append(wrap);
        }
        for (const warning of report.warnings || []) validationInfo.append(node('p', 'notice', warning));
      } else validationInfo.textContent = 'Показатели качества на независимой размеченной выборке не опубликованы. Числовые обещания точности не используются.';
    } catch {
      info.replaceChildren(node('strong', '', 'Сведения о текущем режиме недоступны'), node('p', '', 'Не удалось загрузить параметры анализатора. При использовании анализа проверяйте режим, указанный в результате.'));
      $('#method-aspects').replaceChildren(node('p', 'text-small muted no-margin', 'Список тем временно недоступен.'));
      $('#ranking-info').replaceChildren(node('strong', '', 'Параметры расчёта недоступны'), node('p', '', 'Не удалось загрузить актуальные правила рейтинга. Процент и место не следует интерпретировать без сведений о составе выборки.'));
    }
  }

  const initializers = {home: initHome, reviews: initReviews, analysis: initAnalysis, university: initUniversity, methodology: initMethodology};
  const initialize = initializers[document.body.dataset.page];
  if (initialize) Promise.resolve(initialize()).catch(error => {
    const container = $('#university-list') || $('#review-list') || $('#university-detail');
    if (container) fail(container, error);
  });
})();
