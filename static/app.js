/* 缠论K线分析 前端逻辑 */
(function () {
  const PERIODS = ['1m', '5m', '15m', '1H', '4H', '1D', '1W'];
  const state = {
    inst: localStorage.getItem('chan_inst') || 'ETH-USDT-SWAP',
    bar: '4H',
    data: null,
    loading: false,
  };

  function beichiView(b) {
    // 旧宽口径数据、缺失或未知分类都保守展示为动能衰减。
    const kind = b.struct_ok === false || !['trend', 'panzheng'].includes(b.kind)
      ? 'momentum' : b.kind;
    const bottom = b.dir === 'down';
    const views = {
      trend: {
        label: `趋势${bottom ? '底' : '顶'}背驰`, color: '#9a6700', rgb: '154,103,0',
        note: '两个同向中枢对应离开段的力度比较。属于趋势背驰候选，仍需后续确认，不代表趋势必然反转。',
      },
      panzheng: {
        label: `盘整${bottom ? '底' : '顶'}背驰`, color: '#3265a8', rgb: '50,101,168',
        note: '同一已形成中枢关联的两次同向波动比较，只提示局部力度减弱，不直接生成一类买卖点。',
      },
      momentum: {
        label: `${bottom ? '下跌' : '上涨'}动能衰减`, color: '#575f6b', rgb: '87,95,107',
        note: '比较段出现力度减弱，但中枢结构不足以确认背驰，不对应一类买卖点。',
      },
    };
    return { kind, ...views[kind] };
  }

  /* ---------------- 图表初始化 ---------------- */
  klinecharts.registerOverlay({
    name: 'biLine',
    totalStep: 3,
    lock: true,
    createPointFigures: ({ coordinates, overlay }) => {
      if (coordinates.length < 2) return [];
      const d = overlay.extendData || {};
      return [{
        type: 'line',
        attrs: { coordinates },
        styles: { color: d.color || '#f0b90b', size: d.size || 1.4, style: d.dashed ? 'dashed' : 'solid', dashedValue: [4, 4] },
        ignoreEvent: true,
      }];
    },
  });

  klinecharts.registerOverlay({
    name: 'zsRect',
    totalStep: 3,
    lock: true,
    createPointFigures: ({ coordinates, overlay }) => {
      if (coordinates.length < 2) return [];
      const [a, b] = coordinates;
      const pending = overlay.extendData?.locked !== true;
      return [{
        type: 'rect',
        attrs: { x: Math.min(a.x, b.x), y: Math.min(a.y, b.y), width: Math.abs(b.x - a.x), height: Math.abs(b.y - a.y) },
        styles: { style: 'stroke_fill', color: pending ? 'rgba(76,141,255,0.04)' : 'rgba(76,141,255,0.10)', borderColor: pending ? 'rgba(76,141,255,0.50)' : 'rgba(76,141,255,0.85)', borderSize: 1, borderStyle: pending ? 'dashed' : 'solid', borderDashedValue: [4, 4] },
        ignoreEvent: true,
      }];
    },
  });

  klinecharts.registerOverlay({
    name: 'chanMark',
    totalStep: 2,
    lock: true,
    createPointFigures: ({ coordinates, overlay }) => {
      const c = coordinates[0];
      if (!c) return [];
      const d = overlay.extendData || {};
      const above = d.pos === 'above';
      const off = 6 + (d.offset || 0);  // offset 用于避免与同点其他标签重叠
      return [{
        type: 'text',
        attrs: { x: c.x, y: above ? c.y - off : c.y + off, text: d.text || '', align: 'center', baseline: above ? 'bottom' : 'top' },
        styles: {
          color: d.textColor || '#fff', size: d.size || 11, weight: 'bold', family: 'sans-serif',
          backgroundColor: d.bg || 'transparent', borderRadius: 3,
          paddingLeft: 4, paddingRight: 4, paddingTop: 2, paddingBottom: 2,
        },
        ignoreEvent: true,
      }];
    },
  });

  // 国内惯例 RSI(Wilder 平滑),与 OKX 显示一致;内置 RSI 用简单均值,数值偏差大
  klinecharts.registerIndicator({
    name: 'RSI_CN',
    shortName: 'RSI',
    calcParams: [6, 12, 24],
    figures: [
      { key: 'rsi1', title: 'RSI6: ', type: 'line' },
      { key: 'rsi2', title: 'RSI12: ', type: 'line' },
      { key: 'rsi3', title: 'RSI24: ', type: 'line' },
    ],
    calc: (dataList, indicator) => {
      const result = dataList.map(() => ({}));
      indicator.calcParams.forEach((n, pi) => {
        let up = 0, total = 0;
        for (let i = 1; i < dataList.length; i++) {
          const diff = dataList[i].close - dataList[i - 1].close;
          const u = Math.max(diff, 0);
          const a = Math.abs(diff);
          up = (up * (n - 1) + u) / n;
          total = (total * (n - 1) + a) / n;
          result[i]['rsi' + (pi + 1)] = total === 0 ? 50 : (up / total) * 100;
        }
      });
      return result;
    },
  });

  const chart = klinecharts.init('chart');
  window._chart = chart;  // 调试用
  chart.setStyles({
    grid: { horizontal: { color: '#161b22' }, vertical: { color: '#161b22' } },
    candle: {
      bar: {
        upColor: '#2ebd85', downColor: '#f6465d', noChangeColor: '#8b949e',
        upBorderColor: '#2ebd85', downBorderColor: '#f6465d', noChangeBorderColor: '#8b949e',
        upWickColor: '#2ebd85', downWickColor: '#f6465d', noChangeWickColor: '#8b949e',
      },
      priceMark: {
        high: { color: '#8b949e' }, low: { color: '#8b949e' },
        last: { upColor: '#2ebd85', downColor: '#f6465d', noChangeColor: '#8b949e' },
      },
      tooltip: { text: { color: '#c9d1d9' } },
    },
    indicator: {
      ohlc: { upColor: '#2ebd85', downColor: '#f6465d' },
      bars: [{ style: 'fill', borderStyle: 'solid', borderSize: 1, borderDashedValue: [2, 2], upColor: 'rgba(46,189,133,.7)', downColor: 'rgba(246,70,93,.7)', noChangeColor: '#8b949e' }],
      tooltip: { text: { color: '#c9d1d9' } },
    },
    xAxis: { axisLine: { color: '#2a2e35' }, tickText: { color: '#8b949e' }, tickLine: { color: '#2a2e35' } },
    yAxis: { axisLine: { color: '#2a2e35' }, tickText: { color: '#8b949e' }, tickLine: { color: '#2a2e35' } },
    separator: { color: '#2a2e35' },
    crosshair: {
      horizontal: { line: { color: '#4b5563' }, text: { backgroundColor: '#2a2e35' } },
      vertical: { line: { color: '#4b5563' }, text: { backgroundColor: '#2a2e35' } },
    },
  });

  // 主图 EMA(5,10,20,40),配色对齐 OKX:白/红/浅蓝/橘黄;副图 VOL/MACD/KDJ/RSI
  chart.createIndicator({
    name: 'EMA', calcParams: [5, 10, 20, 40],
    styles: {
      // 注意:必须给完整线型对象,只给 color 会覆盖掉默认的 size/dashedValue 导致绘制崩溃
      lines: ['#ffffff', '#f6465d', '#6fb3ff', '#ff9f1a'].map((color) => ({
        style: 'solid', smooth: false, size: 1, color, dashedValue: [2, 2],
      })),
    },
  }, true, { id: 'candle_pane' });
  chart.createIndicator('VOL', false, { height: 70 });
  chart.createIndicator('MACD', false, { height: 90 });
  chart.createIndicator('KDJ', false, { height: 80 });
  chart.createIndicator('RSI_CN', false, { height: 80 });

  /* ---------------- 工具栏 ---------------- */
  const $ = (id) => document.getElementById(id);

  function renderCoins(list) {
    const box = $('coins');
    box.innerHTML = '';
    list.forEach((inst) => {
      const b = document.createElement('button');
      b.textContent = inst.split('-')[0];
      b.dataset.inst = inst;
      if (inst === state.inst) b.classList.add('active');
      b.onclick = () => { state.inst = inst; syncActive(); loadData(false); };
      box.appendChild(b);
    });
  }

  function renderPeriods() {
    const box = $('periods');
    PERIODS.forEach((p) => {
      const b = document.createElement('button');
      b.textContent = p;
      b.dataset.bar = p;
      if (p === state.bar) b.classList.add('active');
      b.onclick = () => { state.bar = p; syncActive(); loadData(false); };
      box.appendChild(b);
    });
  }

  function syncActive() {
    document.querySelectorAll('#coins button').forEach((b) => b.classList.toggle('active', b.dataset.inst === state.inst));
    document.querySelectorAll('#periods button').forEach((b) => b.classList.toggle('active', b.dataset.bar === state.bar));
    $('title').textContent = state.inst.replace('-SWAP', ' 永续');
    document.title = `${state.inst} ${state.bar} · 缠论分析`;
    localStorage.setItem('chan_inst', state.inst);  // 与多级别页共享当前币种
  }

  $('goBtn').onclick = () => {
    const v = $('instInput').value.trim().toUpperCase();
    if (!v) return;
    state.inst = v.includes('-') ? v : `${v}-USDT-SWAP`;
    syncActive();
    loadData(false);
  };
  $('instInput').addEventListener('keydown', (e) => { if (e.key === 'Enter') $('goBtn').onclick(); });
  $('refreshBtn').onclick = () => loadData(true);
  ['ckFx', 'ckBi', 'ckXd', 'ckZs', 'ckBc', 'ckDl', 'ckBsp', 'ckTri'].forEach((id) => { $(id).onchange = drawOverlays; });

  /* ---------------- 数据加载 ---------------- */
  async function loadData(force) {
    if (state.loading) return;
    state.loading = true;
    const btn = $('refreshBtn');
    btn.disabled = true;
    setStatus('加载中…');
    try {
      const r = await fetch(`/api/kline?inst=${encodeURIComponent(state.inst)}&bar=${state.bar}&force=${force ? 1 : 0}`);
      const j = await r.json();
      if (j.code !== 0) throw new Error(j.msg || '未知错误');
      state.data = j;
      applyData();
      setStatus(`${j.count} 根K线 · 更新于 ${new Date(j.fetched_at).toLocaleTimeString()}`);
    } catch (e) {
      setStatus(`加载失败: ${e.message}`, true);
    } finally {
      state.loading = false;
      btn.disabled = false;
    }
  }

  function setStatus(msg, err) {
    const el = $('status');
    el.textContent = msg;
    el.className = err ? 'err' : '';
  }

  function applyData() {
    const d = state.data;
    const last = d.candles[d.candles.length - 1];
    const p = last.c;
    const prec = p >= 1000 ? 2 : p >= 10 ? 3 : p >= 0.1 ? 5 : 8;
    const newList = d.candles.map((c) => ({
      timestamp: c.ts, open: c.o, high: c.h, low: c.l, close: c.c, volume: c.vol,
    }));
    const key = `${d.inst}|${d.bar}`;
    const prevLast = state.lastList && state.lastList[state.lastList.length - 1];
    // 同币种同周期的刷新走增量更新(只改最后一根和新增K线),
    // 保留用户当前的缩放与拖动位置;整体重载只在切币/切周期时发生
    if (state.chartKey === key && prevLast
        && newList.some((k) => k.timestamp === prevLast.timestamp)) {
      newList.filter((k) => k.timestamp >= prevLast.timestamp)
        .forEach((k) => chart.updateData(k));
    } else {
      chart.setPriceVolumePrecision(prec, 2);
      chart.applyNewData(newList);
    }
    state.chartKey = key;
    state.lastList = newList;
    drawOverlays();
    renderPanel();
    renderDecision();
  }

  /* ---------------- 交易决策 ---------------- */
  function renderDecision() {
    const d = state.data && state.data.decision;
    const el = $('decision');
    if (!d) { el.style.display = 'none'; return; }
    el.style.display = 'flex';
    const colorMap = { long: '#2ebd85', short: '#f6465d', wait: '#8b949e' };
    const nameMap = { long: '可考虑做多', short: '可考虑做空', wait: '观望' };
    const c = colorMap[d.action];
    el.style.borderLeftColor = c;

    const blk = (lbl, val) => `<span class="blk"><span class="lbl">${lbl}</span>${val}</span>`;
    let html = `<span class="act" style="color:${c}">${nameMap[d.action]}</span>`;
    html += blk(`大级别方向(${d.dir_level})`, d.dir_desc);

    if (d.action === 'wait') {
      html += `<span class="blk" style="white-space:normal;max-width:640px">${d.reason}</span>`;
    } else {
      html += blk('信号', `${d.signal.type} @ ${fmtP(d.signal.price)}(质量 ${d.signal_quality})${d.signal.locked === false ? ' <span class="warn">未锁定·或移动</span>' : ''}`);
      html += blk('指标确认', `${d.confirm_hits}/4:${d.confirms.join('、')}`);
      html += blk('质量分 q', `<b style="color:${c}">${d.q}</b> → 建议标准仓位的 ${d.position}%`);
      html += blk('入场参考', fmtP(d.entry));
      html += blk(d.stop_name, `<span class="down">${fmtP(d.stop)}</span>`);
      if (d.targets.length) {
        html += blk('目标', d.targets.map((t) => `${t[0]} ${fmtP(t[1])}`).join(' → '));
      }
      if (d.rr != null) html += blk('盈亏比', `1:${d.rr}`);
      (d.warnings || []).forEach((w) => { html += `<span class="blk warn" style="white-space:normal;max-width:360px">⚠ ${w}</span>`; });
    }
    html += '<button id="calcOpen">仓位计算器</button>';
    html += `<span class="blk muted" style="white-space:normal;max-width:420px">${d.rules}。仅供参考,非投资建议。</span>`;
    el.innerHTML = html;
    $('calcOpen').onclick = openCalc;
  }

  /* ---------------- 缠论标注 ---------------- */
  function drawOverlays() {
    chart.removeOverlay({ groupId: 'chan' });
    const d = state.data;
    if (!d) return;
    const ch = d.chan;
    const mk = (o) => chart.createOverlay(Object.assign({ groupId: 'chan', lock: true }, o));

    if ($('ckBi').checked) {
      ch.bi.filter((b) => b.state !== 'blocked').forEach((b) => mk({
        name: 'biLine',
        points: [
          { timestamp: b.start_ts, value: b.start_price },
          { timestamp: b.end_ts, value: b.end_price },
        ],
        extendData: { layer: 'bi', color: b.unfinished ? '#8b949e' : '#f0b90b', dashed: b.locked !== true },
      }));
    }
    if ($('ckXd').checked) {
      (ch.xianduan || []).forEach((b) => mk({
        name: 'biLine',
        points: [{ timestamp: b.start_ts, value: b.start_price }, { timestamp: b.end_ts, value: b.end_price }],
        extendData: { layer: 'xianduan', color: '#b388ff', size: 2.5, dashed: b.locked !== true },
      }));
    }
    if ($('ckFx').checked) {
      ch.fenxing.forEach((f) => mk({
        name: 'chanMark',
        points: [{ timestamp: f.ts, value: f.price }],
        extendData: f.type === 'top'
          ? { text: '▿', pos: 'above', textColor: '#8b949e', size: 10 }
          : { text: '▵', pos: 'below', textColor: '#8b949e', size: 10 },
      }));
    }
    if ($('ckZs').checked) {
      (ch.zhongshu_display || ch.zhongshu).forEach((z) => mk({
        name: 'zsRect',
        points: [
          { timestamp: z.start_ts, value: z.zg },
          { timestamp: z.end_ts, value: z.zd },
        ],
        extendData: { locked: z.locked, state: z.state },
      }));
    }
    if ($('ckBc').checked) {
      ch.beichi.forEach((b) => {
        const unlocked = b.locked === false;
        const view = beichiView(b);
        const bg = unlocked ? `rgba(${view.rgb},0.55)` : view.color;
        const tail = unlocked ? ' ?' : '';
        mk({
          name: 'chanMark',
          points: [{ timestamp: b.ts, value: b.price }],
          extendData: {
            text: `${view.kind === 'momentum' ? '' : '⚡'}${view.label} ${b.area_ratio}${tail}`,
            pos: b.dir === 'down' ? 'below' : 'above', bg, textColor: '#fff', offset: 20,
          },
        });
      });
    }
    if ($('ckDl').checked && ch.beili) {
      ch.beili.forEach((s) => {
        const buy = s.type === 'DB';
        const unlocked = s.locked === false;
        const hidden = s.subtype === 'hidden';
        // 青色=背离买点,洋红=背离卖点;隐藏背离(中继)加「隐」前缀
        const solid = buy ? '#1b7c83' : '#bf3989';
        const faded = buy ? 'rgba(27,124,131,0.5)' : 'rgba(191,57,137,0.5)';
        mk({
          name: 'chanMark',
          points: [{ timestamp: s.ts, value: s.price }],
          extendData: {
            text: (hidden ? '隐' : '') + (buy ? '背离B' : '背离S') + ` ${s.votes.length}票` + (unlocked ? '?' : ''),
            pos: buy ? 'below' : 'above', offset: 34,
            bg: unlocked ? faded : solid, textColor: '#fff',
          },
        });
      });
    }
    if ($('ckTri').checked) {
      (d.patterns || []).forEach((p) => {
        if (p.type !== 'triangle') return;
        // 收敛三角形:上轨 + 下轨
        const triColor = p.break_dir === 'up' ? '#2ebd85' : p.break_dir === 'down' ? '#f6465d' : '#8250df';
        [p.upper, p.lower].forEach((ln) => mk({
          name: 'biLine',
          points: [
            { timestamp: ln[0][0], value: ln[0][1] },
            { timestamp: ln[1][0], value: ln[1][1] },
          ],
          extendData: { color: triColor, dashed: true },
        }));
        mk({
          name: 'chanMark',
          points: [{ timestamp: p.upper[1][0], value: p.upper[1][1] }],
          extendData: { text: '收敛' + (p.break_dir ? (p.break_dir === 'up' ? '↑破' : '↓破') : '△'), pos: 'above', offset: 4, bg: triColor, textColor: '#fff' },
        });
      });
    }
    if ($('ckBsp').checked) {
      ch.bsp.forEach((s) => {
        const buy = s.type[0] === 'B';
        const unlocked = s.locked === false;
        mk({
          name: 'chanMark',
          points: [{ timestamp: s.ts, value: s.price }],
          extendData: {
            text: s.type + (unlocked ? '?' : ''), pos: buy ? 'below' : 'above',
            bg: unlocked ? (buy ? 'rgba(26,127,55,0.5)' : 'rgba(207,34,46,0.5)')
                         : (buy ? '#1a7f37' : '#cf222e'),
            textColor: '#fff',
          },
        });
      });
    }
  }

  /* ---------------- 分析面板 ---------------- */
  const fmtTs = (ts) => {
    const dt = new Date(ts);
    const pad = (n) => String(n).padStart(2, '0');
    return `${dt.getMonth() + 1}/${dt.getDate()} ${pad(dt.getHours())}:${pad(dt.getMinutes())}`;
  };
  const fmtP = (v) => (v == null ? '-' : Number(v).toLocaleString(undefined, { maximumFractionDigits: 6 }));

  function renderPanel() {
    const ch = state.data.chan;
    const s = ch.summary || {};

    /* 结构状态 */
    const posMap = { above_zg: ['中枢上方', 'up'], inside: ['中枢内部', 'flat'], below_zd: ['中枢下方', 'down'] };
    let html = '';
    if (s.last_bi) {
      const up = s.last_bi.dir === 'up';
      html += kv('最新成笔', `<span class="${up ? 'up' : 'down'}">${up ? '↑ 向上' : '↓ 向下'}</span> ${fmtP(s.last_bi.start_price)} → ${fmtP(s.last_bi.end_price)} <span class="muted">${s.last_bi.locked ? '已锁定' : '可调整'}</span>`);
    }
    const unf = ch.bi.find((b) => b.unfinished);
    if (unf) {
      const up = unf.dir === 'up';
      html += kv(unf.state === 'extending' ? '原笔延伸候选' : '候选尾部', `<span class="${up ? 'up' : 'down'}">${up ? '↑' : '↓'}</span> ${fmtP(unf.start_price)} → ${fmtP(unf.end_price)} <span class="muted">(${unf.state === 'blocked' ? '等待合法分型接续' : '未确认'})</span>`);
    }
    if (s.last_zs) {
      html += kv('最近中枢 ZG/ZD', `${fmtP(s.last_zs.zg)} / ${fmtP(s.last_zs.zd)}${s.last_zs.extending ? ' <span class="muted">(延伸中)</span>' : ''}`);
      html += kv('震荡区间 GG/DD', `${fmtP(s.last_zs.gg)} / ${fmtP(s.last_zs.dd)}`);
      html += kv('中枢状态', `${s.last_zs.locked ? '构成笔已锁定' : '构成笔可调整'}${s.last_zs.bi_count > 9 ? ` · 连续延伸 ${s.last_zs.bi_count} 笔` : ''}`);
      if (s.last_zs.known_at) html += kv('中枢最早可知', fmtTs(s.last_zs.known_at));
      if (s.price_pos) {
        const [txt, cls] = posMap[s.price_pos] || ['-', 'flat'];
        html += kv('现价位置', `<span class="${cls}">${txt}</span> (${fmtP(s.last_price)})`);
      }
    }
    html += kv('统计', `笔 ${ch.bi.filter((b) => !b.unfinished).length} · 线段 ${(ch.xianduan || []).filter((b) => b.locked).length} 已确认 · 中枢 ${(ch.zhongshu_display || ch.zhongshu).length}`);
    html += '<div class="muted" style="margin-top:6px">黄线=笔，紫色粗线=线段；虚线可调整。中枢仍按笔构建，矩形左沿是回溯起点，不是当时已知时间。</div>';
    $('colStruct').innerHTML = '<h3>结构状态</h3>' + html;

    /* 量价·主力 */
    const sg = state.data.signals || {};
    let volHtml = '';
    if (sg.volume) {
      const v = sg.volume;
      const vsColor = v.state === '放量' ? '#d29922' : v.state === '缩量' ? '#6e7681' : '#4c8dff';
      volHtml += kv('量比(量/MA20)', `<b>${v.ratio}</b> <span class="tag" style="background:${vsColor}">${v.state}</span>`);
      volHtml += kv('量价组合', `<b>${v.combo}</b>`);
      volHtml += `<div class="muted" style="margin-bottom:4px">${v.combo_note}</div>`;
      volHtml += kv('位置(近60根)', `${v.zone} (${v.pos}%)`);
      const zl = v.zhuli;
      const zColor = zl.conclusion.includes('吸筹') ? '#2ebd85' : zl.conclusion.includes('派发') ? '#f6465d' : '#8b949e';
      volHtml += `<div class="card" style="margin-top:6px">
        <div style="font-weight:600;color:${zColor}">${zl.conclusion}</div>
        ${zl.evidence.map((e) => `<div class="muted">· ${e}</div>`).join('')}
      </div>`;
      volHtml += '<div class="muted">仅基于K线量价;CVD/持仓量/链上数据未接入。</div>';
    } else volHtml = '<div class="muted">数据不足。</div>';
    $('colVol').innerHTML = '<h3>量价 · 主力</h3>' + volHtml;

    /* KDJ · RSI */
    let krHtml = '';
    if (sg.kdj) {
      const K = sg.kdj;
      const kTag = (t) => {
        const c = t.includes('金叉') ? '#2ebd85' : t.includes('死叉') ? '#f6465d'
          : t.includes('超买') || t.includes('高位') ? '#d29922'
          : t.includes('超卖') || t.includes('低位') ? '#4c8dff' : '#6e7681';
        return `<span class="tag" style="background:${c};margin-right:4px">${t}</span>`;
      };
      krHtml += kv('KDJ(9,3,3)', `K ${K.k} · D ${K.d} · J ${K.j}`);
      krHtml += `<div style="margin:2px 0 8px">${K.states.map(kTag).join('')}</div>`;
      const R = sg.rsi;
      krHtml += kv('RSI(6/12/24)', `${R.r6} / ${R.r12} / ${R.r24}`);
      krHtml += `<div style="margin:2px 0 4px">${kTag(R.state)}</div>`;
      if (R.divergence) {
        const dv = R.divergence;
        const c = dv.side === 'buy' ? '#2ebd85' : '#f6465d';
        krHtml += `<div class="card"><div style="font-weight:600;color:${c}">RSI ${dv.type}</div>
          <div class="muted">${dv.note}</div></div>`;
      } else {
        krHtml += '<div class="muted">最近笔端点间未检测到 RSI 背离。</div>';
      }
    } else krHtml = '<div class="muted">数据不足。</div>';
    $('colKR').innerHTML = '<h3>KDJ · RSI</h3>' + krHtml;

    /* 背驰与动能衰减 */
    let bcHtml = '';
    const bcs = ch.beichi.slice(-2).reverse();
    if (!bcs.length) bcHtml = '<div class="muted">当前范围内未检测到符合条件的背驰或动能衰减；判定同时检查比较段、价格新极值与同向 MACD 面积。</div>';
    bcs.forEach((b) => {
      const view = beichiView(b);
      const scoreColor = b.score >= 85 ? '#2ebd85' : b.score >= 65 ? '#4c8dff' : b.score >= 40 ? '#d29922' : '#6e7681';
      const lvTxt = b.score >= 85 ? '评分高' : b.score >= 65 ? '评分较高' : b.score >= 40 ? '评分一般' : '评分偏低';
      const lockTag = b.locked === false
        ? '<span class="tag" style="background:#d29922">未锁定·或移动</span>'
        : '<span class="tag" style="background:#2a2e35;color:#8b949e">已锁定</span>';
      const comparison = b.comparison || {};
      const segment = (label, leg, area) => leg
        ? `<div style="margin-top:5px">${kv(label, `${fmtP(leg.start_price)} → ${fmtP(leg.end_price)}`)}<div class="muted">${fmtTs(leg.start_ts)} → ${fmtTs(leg.end_ts)} · 同向柱面积 ${fmtP(area)}</div></div>`
        : '';
      const comparisonHtml = segment('前段', comparison.before, b.area_in)
        + segment('后段', comparison.after, b.area_out);
      bcHtml += `<div class="card">
        <div class="kv"><span><span class="tag" style="background:${view.color};font-weight:600">${view.label}</span> ${lockTag}</span>
        <span class="muted">${fmtTs(b.ts)}</span></div>
        ${kv('价格', fmtP(b.price))}
        ${kv('MACD 面积比', `<b>${b.area_ratio}</b> <span class="muted">(后段 / 前段，力度门槛 &lt;0.7)</span>`)}
        ${comparisonHtml || '<div class="muted">暂无比较段明细，请刷新数据。</div>'}
        ${kv('评分', `<b style="color:${scoreColor}">${b.score}</b> · ${lvTxt} <span class="muted">(区间套未验证)</span>`)}
        <div class="scorebar"><div style="width:${Math.min(b.score, 100)}%;background:${scoreColor}"></div></div>
        <div class="muted" style="margin-top:4px">${view.note}</div>
      </div>`;
    });
    /* 形态卡片(收敛三角形) */
    (state.data.patterns || []).forEach((p) => {
      if (p.type !== 'triangle') return;
      const c = p.break_dir === 'up' ? '#2ebd85' : p.break_dir === 'down' ? '#f6465d' : '#8250df';
      bcHtml += `<div class="card">
        <div class="kv"><span style="font-weight:600;color:${c}">收敛三角形 · ${p.status}</span></div>
        ${kv('上轨(当前)', fmtP(p.upper_now))}
        ${kv('下轨(当前)', fmtP(p.lower_now))}
        <div class="muted" style="margin-top:4px">${p.note}</div>
      </div>`;
    });
    $('colBc').innerHTML = '<h3>背驰 · 动能衰减 · 形态</h3>' + bcHtml;

    /* 买卖点 + 指标背离(合并按时间排序) */
    const sigItems = ch.bsp.map((p) => ({ ...p, _dl: false }))
      .concat((ch.beili || []).map((p) => ({ ...p, _dl: true })))
      .sort((a, b) => a.k_idx - b.k_idx);
    let rows = '';
    sigItems.slice(-18).reverse().forEach((p) => {
      const buy = p.type[0] === 'B' || p.type === 'DB';
      const unlocked = p.locked === false;
      const sc = p.score;
      const scColor = sc >= 75 ? '#2ebd85' : sc >= 60 ? '#4c8dff' : sc >= 45 ? '#d29922' : '#6e7681';
      let tag, tagBg, note;
      if (p._dl) {
        tag = (p.subtype === 'hidden' ? '隐' : '') + (buy ? '背离B' : '背离S');
        tagBg = buy ? '#1b7c83' : '#bf3989';
        note = `${p.votes.length}票(${p.votes.join('·')})。${p.note}`;
      } else {
        tag = p.type;
        tagBg = buy ? '#1a7f37' : '#cf222e';
        note = (p.confirm_n ? `共振${p.confirm_n}票:${p.confirms.join('·')}。` : '') + p.note;
      }
      rows += `<tr class="row" data-ts="${p.ts}">
        <td><span class="tag" style="background:${tagBg};${unlocked ? 'opacity:.55' : ''}">${tag}${unlocked ? '?' : ''}</span></td>
        <td>${fmtP(p.price)}</td>
        <td>${sc != null ? `<b style="color:${scColor}">${sc}</b> <span class="muted">${p.grade || ''}</span>` : '-'}</td>
        <td>${unlocked ? '<span class="tag" style="background:#d29922">未锁定</span>' : '<span class="muted">已锁定</span>'}</td>
        <td class="muted">${fmtTs(p.ts)}</td>
        <td class="muted" style="font-size:11px">${note}</td></tr>`;
    });
    $('colBsp').innerHTML = '<h3>买卖点 · 指标背离(点击跳转)</h3>' +
      (rows ? `<table><tr><th>类型</th><th>价格</th><th>评分</th><th>状态</th><th>时间</th><th>说明</th></tr>${rows}</table>`
            : '<div class="muted">当前范围内未检测到买卖点。</div>');
    document.querySelectorAll('#colBsp tr.row').forEach((tr) => {
      tr.onclick = () => chart.scrollToTimestamp(Number(tr.dataset.ts), 300);
    });
  }

  function kv(k, v) {
    return `<div class="kv"><span class="k">${k}</span><span>${v}</span></div>`;
  }

  /* ---------------- 仓位计算器 ---------------- */
  $('modalClose').onclick = () => { $('modalMask').style.display = 'none'; };
  $('modalMask').onclick = (e) => { if (e.target === $('modalMask')) $('modalMask').style.display = 'none'; };

  function openCalc() {
    // 用当前决策的方向/入场/止损预填,决策为观望时留空手填
    const d = (state.data && state.data.decision) || {};
    if (d.action && d.action !== 'wait') $('cSide').value = d.action;
    const entry = d.entry ?? d.better_entry ?? (state.data && state.data.candles[state.data.candles.length - 1].c);
    if (entry) $('cEntry').value = entry;
    if (d.stop) $('cStop').value = d.stop;
    $('modalMask').style.display = 'flex';
    runCalc();
  }

  function runCalc() {
    const side = $('cSide').value;
    const eq = +$('cEq').value, riskPct = +$('cRisk').value, lev = +$('cLev').value;
    const entry = +$('cEntry').value, stop = +$('cStop').value;
    const out = $('calcOut');
    if (!(eq > 0 && riskPct > 0 && lev > 0 && entry > 0 && stop > 0)) {
      out.innerHTML = '<div class="muted">填写完整参数后自动计算。</div>';
      return;
    }
    const D = Math.abs(entry - stop);
    if (D === 0) { out.innerHTML = '<div class="warn">入场价与止损价不能相同。</div>'; return; }
    const R = eq * riskPct / 100;          // 单笔最大亏损
    const Q = R / D;                        // 数量(币)
    const notional = Q * entry;             // 名义价值
    const M = notional / lev;               // 保证金
    const MMR = 0.005;                      // 维持保证金率(近似)
    const liq = side === 'long' ? entry * (1 - 1 / lev + MMR) : entry * (1 + 1 / lev - MMR);
    const liqDist = Math.abs(entry - liq);
    const stopOk = D < liqDist / 3;         // 手册硬约束:止损距离 < 强平距离÷3
    const f = (v, n = 4) => Number(v.toPrecision(6)).toLocaleString(undefined, { maximumFractionDigits: n });
    out.innerHTML =
      kv('最大亏损 R', `${f(R, 2)} USDT(权益的 ${riskPct}%)`) +
      kv('开仓数量 Q = R÷|入场-止损|', `${f(Q)} 币(名义 ${f(notional, 2)} USDT)`) +
      kv(`保证金 M(${lev}× 逐仓)`, `${f(M, 2)} USDT`) +
      kv('预估强平价', `${f(liq)}(距入场 ${f(liqDist / entry * 100, 2)}%)`) +
      kv('止损距离 vs 强平距离÷3',
        stopOk ? `<span class="up">✓ ${f(D / entry * 100, 2)}% < ${f(liqDist / 3 / entry * 100, 2)}%,符合手册硬约束</span>`
               : `<span class="down">✗ ${f(D / entry * 100, 2)}% ≥ ${f(liqDist / 3 / entry * 100, 2)}%,违反"止损<强平距离÷3",请降杠杆或收紧止损</span>`) +
      `<div class="muted" style="margin-top:4px">保证金不足名义价值时按数量 Q 下单即可,损失始终锁定为 R;强平价按 MMR≈0.5% 估算,以交易所实际为准。</div>`;
  }
  ['cSide', 'cEq', 'cRisk', 'cLev', 'cEntry', 'cStop'].forEach((id) => { $(id).oninput = runCalc; $(id).onchange = runCalc; });

  /* ---------------- 启动 ---------------- */
  window.addEventListener('resize', () => chart.resize());
  // 每分钟自动刷新(可用工具栏勾选框关闭)
  setInterval(() => {
    if ($('ckAuto').checked && !state.loading) loadData(true);
  }, 60 * 1000);
  renderPeriods();
  fetch('/api/instruments').then((r) => r.json()).then((j) => renderCoins(j.instruments))
    .catch(() => renderCoins(['BTC-USDT-SWAP', 'ETH-USDT-SWAP']));
  syncActive();
  loadData(false);
})();
