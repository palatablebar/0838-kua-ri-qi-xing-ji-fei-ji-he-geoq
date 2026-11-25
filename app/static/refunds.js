(function () {
  var state = { sample: null };
  var $ = function (id) { return document.getElementById(id); };
  function esc(value) {
    return String(value === null || value === undefined ? '' : value).replace(/[&<>"']/g, function (ch) {
      return {"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[ch];
    });
  }
  async function request(url, options) {
    var response = await fetch(url, Object.assign({headers: {'Content-Type': 'application/json'}}, options || {}));
    var data = await response.json().catch(function () { return {}; });
    if (!response.ok) throw new Error(data.error || ('HTTP ' + response.status));
    return data;
  }
  function setNotice(text) { $('notice').textContent = text || ''; }
  var REASONS = {NO_VOUCHER: '无原扣款凭据', VOUCHER_LATE: '凭据晚到', OVER_REFUND: '超退阻断', VOUCHER_MATCHED: '凭据匹配'};
  function renderEntries(data) {
    $('refund-head').innerHTML = '<tr><th>退款流水</th><th>出款日</th><th class="num">金额(分)</th>' +
      '<th>原扣款凭据</th><th>状态</th><th>认领日</th></tr>';
    $('refund-body').innerHTML = data.entries.length ? data.entries.map(function (row) {
      var label = row.status === 'CLAIMED' ? '已退' : '暂挂';
      return '<tr><td>' + esc(row.refund_id) + '</td><td>' + esc(row.paid_on) + '</td>' +
        '<td class="num">' + esc(row.amount_cents) + '</td><td>' + esc(row.voucher_ref || '—') + '</td>' +
        '<td><span class="verdict" data-v="' + esc(row.status) + '">' + label + '</span> ' +
        '<small>' + esc(REASONS[row.reason] || row.reason) + '</small></td>' +
        '<td>' + esc(row.claimed_on || '—') + '</td></tr>';
    }).join('') : '<tr><td class="empty" colspan="6">入账后显示退款条目</td></tr>';
  }
  function renderDays(data) {
    $('refund-day-count').textContent = data.days.length ? data.days.length + ' 个结算日' : '--';
    $('refund-days').innerHTML = data.days.length ? data.days.map(function (day) {
      return '<div class="ledger-row"><span>' + esc(day.paid_on) + '</span><strong>' + esc(day.paid_cents) + ' 分</strong></div>' +
        '<div class="ledger-row sub"><span>已退 / 暂挂</span><strong>' + esc(day.claimed_cents) + ' / ' + esc(day.pending_cents) + '</strong></div>';
    }).join('') : '<p class="empty">日结入账后显示</p>';
  }
  function renderVouchers(data) {
    var rows = data.vouchers.map(function (voucher) {
      return '<div class="ledger-row"><span>' + esc(voucher.voucher_id) + ' · 实扣 ' + esc(voucher.charged_cents) + '</span>' +
        '<strong>已认退 ' + esc(voucher.claimed_cents) + ' · 余 ' + esc(voucher.remaining_cents) + '</strong></div>';
    });
    rows = rows.concat(data.checks.map(function (check) {
      return '<div class="ledger-row"><span>' + esc(check.label) + '</span>' +
        '<strong class="' + (check.ok ? 'check-ok' : 'check-bad') + '">' + (check.ok ? '通过' : '异常') + '</strong></div>';
    }));
    $('refund-voucher-list').innerHTML = rows.length ? rows.join('') : '<p class="empty">凭据到达后显示</p>';
    $('refund-check-state').textContent = data.checks.length ? (data.all_checks ? '对账通过' : '存在异常') : '';
  }
  function renderBatch(data) {
    var booked = data.batches.filter(function (batch) { return batch.status === 'BOOKED'; });
    var label = data.batches.length ? data.batches.map(function (batch) {
      return batch.batch_id + ' · ' + (batch.status === 'ISSUED' ? '已签发' : '已入账');
    }).join('，') : '未入账';
    $('refund-batch-status').textContent = label;
    $('refund-receive').disabled = !data.batches.length;
    $('refund-issue').disabled = !booked.length;
  }
  function render(data) {
    renderEntries(data);
    renderDays(data);
    renderVouchers(data);
    renderBatch(data);
  }
  async function refresh() {
    render(await request('/api/refunds/workbench'));
  }
  async function book() {
    try {
      setNotice('');
      state.sample = state.sample || await request('/api/refunds/sample');
      var result = await request('/api/refunds/batches', {method: 'POST', body: JSON.stringify(state.sample.batch)});
      setNotice('日结入账 ' + result.booked + ' 笔' + (result.duplicates ? '，重复 ' + result.duplicates + ' 笔未重复记账' : ''));
      await refresh();
    } catch (error) { setNotice(error.message); }
  }
  async function receive() {
    try {
      setNotice('');
      state.sample = state.sample || await request('/api/refunds/sample');
      var result = await request('/api/refunds/vouchers', {method: 'POST', body: JSON.stringify(state.sample.late)});
      setNotice(result.claimed_total ? '凭据补到，认领 ' + result.claimed_total + ' 笔暂挂' : '凭据已登记，无新增认领');
      await refresh();
    } catch (error) { setNotice(error.message); }
  }
  async function issue() {
    try {
      setNotice('');
      var data = await request('/api/refunds/workbench');
      var booked = data.batches.filter(function (batch) { return batch.status === 'BOOKED'; });
      if (!booked.length) return;
      var result = await request('/api/refunds/batches/' + encodeURIComponent(booked[0].batch_id) + '/issue',
        {method: 'POST', body: '{}'});
      setNotice('日结批次 ' + result.batch_id + ' 已签发，暂挂款仍可在工作台核对');
      await refresh();
    } catch (error) { setNotice(error.message); }
  }
  document.addEventListener('DOMContentLoaded', function () {
    $('refund-book').addEventListener('click', book);
    $('refund-receive').addEventListener('click', receive);
    $('refund-issue').addEventListener('click', issue);
    refresh().catch(function () {});
  });
})();
