import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import test from 'node:test';
import {createContext, runInContext} from 'node:vm';
import {prepareSnapshot} from '../../scripts/lib/public-data.mjs';

// All review data below are invented test fixtures, never the website corpus.
const catalog = JSON.parse(readFileSync(new URL('../../website/data/catalog.json', import.meta.url), 'utf8'));
const rules = {
  version: 'fixture-rules-1', aspect_labels: {teaching: 'Преподавание'},
  aspect_patterns: {teaching: '^преподавател\\w*$'}, positive_pattern: '^хорош\\w*$',
  negative_pattern: '^плох\\w*$', stopwords: ['и', 'но'],
};
const options = {basePath: '/Baknin_Project/', exportedAt: '2026-10-14T13:30:00+03:00'};
function review(changes = {}) {
  return {
    id: 1, university_id: 'timiryazev', text: 'Преподаватели хорошие техническая фикстура 1',
    source: {id: 1, platform: 'Invented test fixtures', url: 'https://example.org/', terms_checked_at: '2026-10-14'},
    source_url: 'https://example.org/fixtures/1', published_at: '2026-01-01', collected_at: '2026-10-14',
    sentiment: 'positive', approved: true, verified: true, personal_data_removed: true, display_allowed: true,
    aspects: [{aspect: 'teaching', sentiment: 'positive', approved: true, context: 'Преподаватели хорошие'}],
    ...changes,
  };
}
const build = records => prepareSnapshot(catalog, records, rules, options);

test('committed public inputs validate without creating additional records', () => {
  const actual = JSON.parse(readFileSync(new URL('../../website/data/reviews.json', import.meta.url), 'utf8'));
  const actualRules = JSON.parse(readFileSync(new URL('../../website/data/rules.json', import.meta.url), 'utf8'));
  const snapshot = prepareSnapshot(catalog, actual, actualRules, options);
  assert.equal(snapshot.universities.items.length, catalog.universities.length);
  assert.equal(snapshot.reviews.length, actual.length);
  assert.deepEqual(snapshot.details, {});
  assert.equal(snapshot.methodology.requires_corpus, actual.length === 0);
  assert.equal(snapshot.methodology.engine, 'browser_rules');
  assert.equal(snapshot.methodology.metrics_available, false);
  assert.equal(snapshot.health.ml_trained, false);
  assert.equal(snapshot.exported_at, '2026-10-14T10:30:00Z');
});

test('manual public records normalize timestamps and optional defaults without input mutation', () => {
  const input = review();
  const original = structuredClone(input);
  const snapshot = build([input]);
  assert.deepEqual(input, original);
  const record = snapshot.reviews[0];
  assert.equal(record.source_review_id, null);
  assert.equal(record.original_score, null);
  assert.equal(record.confidence, null);
  assert.equal(record.model_version, 'manual-labels-v1');
  assert.equal(record.manual_reviewed, true);
  assert.equal(record.needs_review, false);
  assert.equal(record.published_at, '2026-01-01T00:00:00Z');
  assert.equal(record.aspects[0].confidence, null);
  assert.equal(snapshot.methodology.sources[0].review_count, 1);
  record.source.platform = 'caller mutation';
  record.aspects[0].context = 'caller mutation';
  snapshot.universities.items[0].full_name = 'caller mutation';
  snapshot.rules.stopwords.push('caller mutation');
  assert.deepEqual(input, original);
  assert.notEqual(catalog.universities[0].full_name, 'caller mutation');
  assert.deepEqual(rules.stopwords, ['и', 'но']);
});

test('public text remains verbatim so aspect quotes stay exact', () => {
  const text = '  Преподаватели хорошие техническая фикстура 1\n';
  assert.equal(build([review({text})]).reviews[0].text, text);
});

test('hidden text and context must already be null before Git/publication', () => {
  assert.throws(() => build([review({display_allowed: false})]), /текст должен быть null/);
  assert.throws(() => build([review({display_allowed: false, text: null})]), /контекст должен быть null/);
  const safe = review({display_allowed: false, text: null, aspects: [
    {aspect: 'teaching', sentiment: 'positive', approved: true, context: null},
  ]});
  const snapshot = build([safe]);
  assert.equal(snapshot.reviews[0].text, null);
  assert.equal(snapshot.reviews[0].aspects[0].context, null);
});

for (const [label, change] of [
  ['audit', {audit: {email: 'private@example.org'}}],
  ['source metadata', {source: {...review().source, email: 'private@example.org'}}],
  ['aspect metadata', {aspects: [{...review().aspects[0], internal_note: 'private note'}]}],
]) {
  test(`unknown ${label} fields cannot silently enter the export`, () => {
    assert.throws(() => build([review(change)]), /неподдерживаемые поля/);
  });
}

for (const [label, change] of [
  ['unverified origin', {verified: false}], ['missing personal-data confirmation', {personal_data_removed: false}],
  ['unknown university', {university_id: 'unknown'}], ['unsafe ID', {id: 9007199254740992}],
  ['uncertain class', {sentiment: 'uncertain'}], ['missing approval boolean', {approved: 'true'}],
  ['missing display boolean', {display_allowed: undefined}], ['uncalibrated aspect confidence', {aspects: [{...review().aspects[0], confidence: 0.95}]}],
  ['unapproved review with approved topic', {approved: false}], ['unsupported topic', {aspects: [{...review().aspects[0], aspect: 'unknown'}]}],
  ['duplicate topic', {aspects: [review().aspects[0], review().aspects[0]]}],
  ['fake quote', {aspects: [{...review().aspects[0], context: 'Этот текст отсутствует в отзыве'}]}],
  ['bad score', {original_score: Infinity}],
]) {
  test(`invalid ${label} rejected`, () => assert.throws(() => build([review(change)])));
}

test('all three sentiment classes remain distinct and pending counts are honest', () => {
  const snapshot = build([
    review(),
    review({id: 2, text: 'Техническая отрицательная фикстура', sentiment: 'negative', source_url: 'https://example.org/fixtures/2', aspects: []}),
    review({id: 3, text: 'Техническая нейтральная фикстура', sentiment: 'neutral', source_url: 'https://example.org/fixtures/3', approved: false, aspects: []}),
  ]);
  assert.deepEqual(snapshot.reviews.map(record => record.sentiment), ['positive', 'negative', 'neutral']);
  assert.equal(snapshot.methodology.stats.approved, 2);
  assert.equal(snapshot.methodology.stats.pending_moderation, 1);
  assert.equal(snapshot.reviews[2].needs_review, true);
});

for (const [label, change] of [
  ['invalid leap date', {published_at: '2025-02-29'}], ['invalid calendar day', {published_at: '2026-02-30'}],
  ['ambiguous naive time', {published_at: '2026-01-01T10:00:00'}], ['invalid hour', {published_at: '2026-01-01T24:00:00Z'}],
  ['invalid offset', {published_at: '2026-01-01T10:00:00+24:00'}], ['collection before publication', {collected_at: '2025-12-31'}],
  ['HTTP publication', {source_url: 'http://example.org/fixtures/1'}], ['publication with credentials', {source_url: 'https://user:password@example.org/fixtures/1'}],
  ['another publication host', {source_url: 'https://other.example.org/fixtures/1'}], ['backslash in URL', {source_url: 'https://example.org/fixtures\\1'}],
]) {
  test(`${label} rejected`, () => assert.throws(() => build([review(change)])));
}

test('source ID and canonical URL bindings are consistent', () => {
  const second = review({id: 2, text: 'Другая техническая фикстура', source_url: 'https://example.org/fixtures/2', aspects: []});
  assert.throws(() => build([review(), {...second, source: {...second.source, platform: 'Renamed fixture platform'}}]), /один ID источника/);
  assert.throws(() => build([review(), {...second, source: {...second.source, id: 2}}]), /другой ID/);
  assert.throws(() => build([review(), {...second, source: {...second.source, url: 'https://example.org/another-platform'}}]), /один ID источника/);
});

test('same physical source ID is rejected globally even after edits or university reassignment', () => {
  const first = review({source_review_id: 'stable-fixture-1'});
  const second = review({id: 2, source_review_id: 'stable-fixture-1', source_url: 'https://example.org/fixtures/edited',
    university_id: 'kubsau', text: 'Отредактированная техническая фикстура', aspects: []});
  assert.throws(() => build([first, second]), /исходная публикация уже учтена/);
  // Mutable platform labels or numeric IDs cannot create a new physical identity.
  const renamed = {...second, source: {...second.source, id: 2, platform: 'Переименованная площадка'}};
  assert.throws(() => build([first, renamed]), /другой ID/);
});

test('source URL is a global fallback physical identity when stable ID is absent', () => {
  const second = review({id: 2, university_id: 'kubsau', text: 'Другая техническая фикстура', aspects: []});
  assert.throws(() => build([review(), second]), /исходная ссылка уже учтена/);
  assert.throws(() => build([review(), {...second, source_url: second.source_url + '#another-fragment'}]), /исходная ссылка уже учтена/);
});

test('shared source pages require distinct stable IDs for every row', () => {
  const first = review({source_review_id: 'fixture-a'});
  const second = review({id: 2, source_review_id: 'fixture-b', university_id: 'kubsau', text: 'Другая техническая фикстура', aspects: []});
  assert.equal(build([first, second]).reviews.length, 2);
  assert.throws(() => build([first, {...second, source_review_id: null}]), /исходная ссылка уже учтена/);
  assert.throws(() => build([{...first, source_review_id: null}, second]), /исходная ссылка уже учтена/);
});

test('normalized duplicates cannot inflate one university but generic cross-university text is allowed', () => {
  const first = review({text: 'Преподаватели хорошие!'});
  const second = review({id: 2, text: '  ПРЕПОДАВАТЕЛИ  хорошие. ', source_url: 'https://example.org/fixtures/2'});
  assert.throws(() => build([first, second]), /повторный нормализованный текст/);
  assert.equal(build([first, {...second, university_id: 'kubsau', aspects: []}]).reviews.length, 2);
});

test('schema settings and unsafe catalogue properties are rejected', () => {
  assert.throws(() => prepareSnapshot({...catalog, schema_version: 2}, [], rules), /версия 1/);
  assert.throws(() => prepareSnapshot({...catalog, private_note: 'private'}, [], rules), /неподдерживаемые поля/);
  for (const settings of [{...catalog.settings, min_reviews: 0}, {...catalog.settings, smoothing: Infinity}, {...catalog.settings, min_topic_reviews: 2}]) {
    assert.throws(() => prepareSnapshot({...catalog, settings}, [], rules));
  }
  for (const id of ['__proto__', 'constructor', 'prototype']) {
    assert.throws(() => prepareSnapshot({...catalog, universities: [{...catalog.universities[0], id}]}, [], rules), /неверный/);
  }
});

test('malformed browser rule expressions stop publication', () => {
  assert.throws(() => prepareSnapshot(catalog, [], {...rules, positive_pattern: '('}), /неверное регулярное выражение/);
  assert.throws(() => prepareSnapshot(catalog, [], {...rules, aspect_patterns: {teaching: '['}}), /неверное регулярное выражение/);
});

test('source fragments cannot bypass canonical source identity', () => {
  const first = review({source_review_id: 'stable-fragment-fixture'});
  const second = review({id: 2, university_id: 'kubsau', source_review_id: 'stable-fragment-fixture',
    text: 'Другая техническая фикстура', source_url: 'https://example.org/fixtures/edited', aspects: [],
    source: {...first.source, id: 2, url: 'https://example.org/#different-fragment'},
  });
  assert.throws(() => build([first, second]), /другой ID/);
});

test('date filters and monthly dynamics use the same Moscow dates visitors see', async () => {
  const snapshot = build([
    review({published_at: '2026-01-31T20:59:59Z'}),
    review({id: 2, text: 'Техническая отрицательная фикстура', sentiment: 'negative',
      source_url: 'https://example.org/fixtures/2', published_at: '2026-02-01T00:00:00+03:00', aspects: []}),
    review({id: 3, text: 'Техническая нейтральная фикстура', sentiment: 'neutral',
      source_url: 'https://example.org/fixtures/3', published_at: '2026-02-01T21:00:00Z', aspects: []}),
  ]);
  const context = createContext({URL, document: {querySelector: () => ({content: '/Baknin_Project/'})},
    fetch: async () => ({ok: true, json: async () => snapshot})});
  runInContext(readFileSync(new URL('../../static/pages-api.js', import.meta.url), 'utf8'), context);
  const request = async path => JSON.parse(JSON.stringify(await context.BakninPagesAPI.request(path)));
  const january = await request('/api/reviews?date_from=2026-01-31&date_to=2026-01-31');
  const february = await request('/api/reviews?date_from=2026-02-01&date_to=2026-02-01');
  assert.deepEqual(january.items.map(record => record.id), [1]);
  assert.deepEqual(february.items.map(record => record.id), [2]);
  const ranking = await request('/api/universities?university_id=timiryazev&date_from=2026-02-01&date_to=2026-02-01');
  assert.equal(ranking.items[0].reviews_total, 1);
  assert.equal(ranking.items[0].negative, 1);
  const detail = await request('/api/universities/timiryazev');
  assert.deepEqual(detail.dynamics, [
    {month: '2026-01', total: 1, positive: 1, negative: 0, neutral: 0, needs_review: 0},
    {month: '2026-02', total: 2, positive: 0, negative: 1, neutral: 1, needs_review: 0},
  ]);
  assert.equal(snapshot.reviews[1].published_at, '2026-01-31T21:00:00Z');
  assert.equal((await request('/api/methodology')).timezone, 'Europe/Moscow');
});
