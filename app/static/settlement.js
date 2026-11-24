import {request, element, pretty} from './client.js';

const BUCKETS = [
  ['paid', '已付', 'PAID'],
  ['payable', '可付', 'PAYABLE'],
  ['disputed', '争议', 'DISPUTED'],
];

const scenarioSelect = document.querySelector('#settle-scenario');
const openButton = document.querySelector('#settle-open');
const closeButton = document.querySelector('#settle-close');
const lateButton = document.querySelector('#settle-late');
const statusLine = document.querySelector('#settle-status');
const counts = document.querySelector('#settle-counts');
const detail = document.querySelector('#settle-detail');
const table = document.querySelector('#settle-rows');

let scenarios = [];
let current = null; // {ledgerId, revision, closed, scenario, snapshot}

function announce(text, failure = false) {
  statusLine.textContent = text;
  statusLine.dataset.failure = String(failure);
}

function currentScenario() {
  return scenarios[Number(scenarioSelect.value)] || null;
}

function renderKpis(snapshot) {
  const totals = snapshot.totals;
  for (const [key] of BUCKETS) {
    document.querySelector(`[data-settle="${key}"]`).textContent = totals[key];
  }
  document.querySelector('[data-settle="coverage"]').textContent = snapshot.valid_coverage;
  document.querySelector('[data-settle="gap"]').textContent = snapshot.gap_cells;
  counts.textContent = `守恒 ${sum(totals)}/${snapshot.valid_coverage} · 缺口 ${snapshot.gap_cells} · r${snapshot.revision ?? '—'}`;
}

function sum(totals) {
  return totals.paid + totals.payable + totals.disputed;
}

function renderRows(snapshot) {
  table.replaceChildren();
  const head = element('tr');
  head.append(
    ...['像元', '范围', '领款供应商', '等级/瓦片', '说明'].map((title) =>
      element('th', title, {scope: 'col'})
    )
  );
  const body = table.createTBody();
  for (const [key, label, code] of BUCKETS) {
    for (const entry of snapshot.buckets[key]) {
      const tr = body.insertRow();
      tr.insertCell().append(element('span', `(${entry.cell.join(',')})`, {class: 'cell-tag'}));
      tr.insertCell().append(element('span', label, {class: `bucket-${code}`}));
      tr.insertCell().textContent = entry.owner ?? '—';
      tr.insertCell().textContent = entry.grade ?? '—';
      tr.insertCell().textContent = entry.retained
        ? `保留原领款人（${entry.tiles?.join('、') || ''}）`
        : entry.suppliers
          ? `跨供应商挂起：${entry.suppliers.join('、')}（${entry.tiles.join('、')}）`
          : entry.tiles?.join('、') || '';
    }
  }
  table.createTHead().append(head);
}

function render(snapshot) {
  renderKpis(snapshot);
  renderRows(snapshot);
  detail.textContent = pretty({
    供应商可付汇总: snapshot.supplier_payable,
    区域像元: snapshot.region_cells,
    覆盖缺口: snapshot.gap_cells,
    分区守恒: snapshot.partition_ok ? '通过：已付/可付/争议互不重叠，并集等于有效覆盖' : '失败',
  });
}

function setButtons(closed) {
  closeButton.disabled = closed;
  lateButton.hidden = !closed;
}

async function loadDemo() {
  const data = await request('/api/settlement/demo');
  scenarios = data.scenarios;
  scenarioSelect.replaceChildren();
  scenarios.forEach((scenario, index) => {
    const option = element('option', scenario.title);
    option.value = String(index);
    scenarioSelect.append(option);
  });
}

async function openLedger() {
  const scenario = currentScenario();
  if (!scenario) return;
  try {
    const created = await request('/api/settlement/ledgers', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({region: scenario.region, tiles: scenario.tiles}),
    });
    const snapshot = await request(
      `/api/settlement/ledgers/${created.id}/preview`,
      {method: 'POST'}
    );
    current = {
      ledgerId: created.id,
      revision: created.revision,
      closed: created.closed,
      scenario,
      snapshot,
    };
    render(snapshot);
    setButtons(created.closed);
    announce(`建账完成：${scenario.title}`);
  } catch (error) {
    announce(error.message, true);
  }
}

async function closeLedger() {
  if (!current) return;
  try {
    const result = await request(`/api/settlement/ledgers/${current.ledgerId}/close`, {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({revision: current.revision}),
    });
    current.revision = result.revision;
    current.closed = result.closed;
    current.snapshot = result.snapshot;
    render(result.snapshot);
    setButtons(true);
    announce('关账成功：可付像元已按领款人付款，争议像元保持挂起');
  } catch (error) {
    announce(`关账被退回：${error.message}`, true);
  }
}

async function acceptLate() {
  if (!current || !current.scenario.late_tiles) {
    announce('该场景没有登记迟到瓦片', true);
    return;
  }
  try {
    const payload = {region: current.scenario.region, tiles: current.scenario.late_tiles};
    const reopened = await request(`/api/settlement/ledgers/${current.ledgerId}/late`, {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify(payload),
    });
    const snapshot = await request(
      `/api/settlement/ledgers/${current.ledgerId}/preview`,
      {method: 'POST'}
    );
    current.revision = reopened.revision;
    current.closed = false;
    current.snapshot = snapshot;
    render(snapshot);
    setButtons(false);
    announce('迟到瓦片已受理并重算：已付款保留原领款人，未付款按新证据裁决');
  } catch (error) {
    announce(error.message, true);
  }
}

export async function initSettlement() {
  await loadDemo();
  openButton.addEventListener('click', openLedger);
  closeButton.addEventListener('click', closeLedger);
  lateButton.addEventListener('click', acceptLate);
  scenarioSelect.addEventListener('change', () => {
    current = null;
    setButtons(false);
    announce('已切换场景，请建账试算');
  });
  await openLedger();
}
