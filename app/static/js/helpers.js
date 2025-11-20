(function (root, factory) {
  if (typeof module === 'object' && module.exports) {
    module.exports = factory();
  } else {
    root.ConnectivityHelpers = factory();
  }
})(typeof self !== 'undefined' ? self : this, function () {
  'use strict';

  function normalizeRows(rows) {
    if (!Array.isArray(rows)) return [];
    const copy = [...rows];
    copy.sort((a, b) => {
      const ta = (a && a.timestamp) || '';
      const tb = (b && b.timestamp) || '';
      if (ta < tb) return -1;
      if (ta > tb) return 1;
      return 0;
    });
    return copy;
  }

  function toComparable(value, isNumeric) {
    if (isNumeric) {
      const num = Number(value);
      if (!Number.isNaN(num)) return num;
    }
    return String(value ?? '');
  }

  function sortData(rows, sortState, toArray, numericCols = new Set()) {
    if (!Array.isArray(rows) || typeof toArray !== 'function') return [];
    const norm = normalizeRows(rows);
    const idx = sortState?.index ?? 0;
    const dir = sortState?.dir === 'desc' ? 'desc' : 'asc';

    norm.sort((a, b) => {
      const arrA = toArray(a) || [];
      const arrB = toArray(b) || [];
      const A = arrA[idx];
      const B = arrB[idx];

      const numeric = numericCols.has(idx);
      const valA = toComparable(A, numeric);
      const valB = toComparable(B, numeric);

      let cmp = 0;
      if (typeof valA === 'number' && typeof valB === 'number') {
        cmp = valA - valB;
      } else {
        cmp = String(valA).localeCompare(String(valB));
      }
      return dir === 'asc' ? cmp : -cmp;
    });

    return norm;
  }

  function csvEscape(val) {
    if (val === null || val === undefined) return '';
    const s = String(val);
    if (/[",\n]/.test(s)) {
      return '"' + s.replace(/"/g, '""') + '"';
    }
    return s;
  }

  function buildCsv(headerRow, rowArrays) {
    const lines = [];
    lines.push(headerRow.map(csvEscape).join(','));
    rowArrays.forEach((row) => {
      lines.push((row || []).map(csvEscape).join(','));
    });
    return lines.join('\n');
  }

  function buildDailyCsv(dailySummary) {
    const header = [
      'date',
      'total_probes',
      'uptime_pct',
      'avg_loss_pct',
      'avg_rtt_ms',
      'min_rtt_ms',
      'max_rtt_ms',
      'down_probes',
      'targets',
      'public_ips'
    ];

    const rows = (dailySummary || []).map((d) => [
      d.date,
      d.total_probes,
      d.uptime_pct,
      d.avg_loss_pct,
      d.avg_rtt_ms,
      d.min_rtt_ms,
      d.max_rtt_ms,
      d.down_probes,
      (d.targets || []).join('; '),
      (d.public_ips || []).join('; ')
    ]);

    return buildCsv(header, rows);
  }

  return {
    normalizeRows,
    sortData,
    csvEscape,
    buildCsv,
    buildDailyCsv,
  };
});
