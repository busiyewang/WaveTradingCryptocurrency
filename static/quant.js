/* 独立界面原型：所有数据均为固定样例，不调用账户、行情或交易 API。 */
(() => {
  'use strict';
  const $ = id => document.getElementById(id);
  const state = {coin: 'ETH', period: '15m', table: 'signals', mode: 'research', timer: null};
  const price = n => (state.coin === 'BTC' ? n * 20 : n).toLocaleString('en-US', {minimumFractionDigits: 2, maximumFractionDigits: 2});
  const time = n => `${String(10 + Math.floor(n / 4)).padStart(2, '0')}:${String(n % 4 * 15).padStart(2, '0')}`;
  function chart() {
    const W = 920, H = 338, left = 16, right = 842, top = 28, bottom = 258;
    const points = Array.from({length: 78}, (_, i) => {
      const trend = 3090 + i * .72 + 18 * Math.sin(i / 8) + 7 * Math.sin(i / 2.9 + (state.period === '4H' ? 1 : 0));
      const o = trend + 3 * Math.sin(i * 2.7), c = trend + 4 * Math.cos(i * 1.8);
      return {o, c, h: Math.max(o, c) + 2 + (i % 5), l: Math.min(o, c) - 2 - (i % 4)};
    });
    const shift = 3142.6 - points[77].c;
    points.forEach(p => { for (const key of ['o','c','h','l']) p[key] += shift; });
    const x = i => left + i * (right - left) / 80;
    const y = p => bottom - (p - 3060) / 130 * (bottom - top);
    let svg = `<svg viewBox="0 0 ${W} ${H}" xmlns="http://www.w3.org/2000/svg" aria-hidden="true"><defs><linearGradient id="volfade" x1="0" y1="0" x2="0" y2="1"><stop stop-color="#70ad90" stop-opacity=".45"/><stop offset="1" stop-color="#70ad90" stop-opacity=".08"/></linearGradient></defs>`;
    [3080, 3100, 3120, 3140, 3160, 3180].forEach(p => { svg += `<path d="M ${left} ${y(p)} H ${right}" stroke="#23313b" stroke-width=".6"/><text x="${right+12}" y="${y(p)+4}" fill="#648090" font-size="10" font-family="monospace">${price(p)}</text>`; });
    [0, 16, 32, 48, 64].forEach((i, k) => { svg += `<path d="M ${x(i)} 18 V 306" stroke="#23313b" stroke-width=".6"/><text x="${x(i)+4}" y="327" fill="#648090" font-size="10" font-family="monospace">${state.period==='15m' ? ['17 Aug','18:00','22:00','18 Aug','06:00'][k] : ['01 Aug','04 Aug','07 Aug','10 Aug','13 Aug'][k]}</text>`; });
    if ($('show-zs').checked) svg += `<rect x="${x(29)}" y="${y(3121)}" width="${x(64)-x(29)}" height="${y(3098)-y(3121)}" fill="#6793c4" fill-opacity=".08" stroke="#5a83a8" stroke-opacity=".55" stroke-dasharray="4 4"/><text x="${x(31)}" y="${y(3121)+15}" fill="#7b9fbf" font-size="10">4H 中枢 · ZG ${price(3121)}</text>`;
    points.forEach((p, i) => { const color = p.c >= p.o ? '#74be9a' : '#cf7b83'; svg += `<path d="M ${x(i)} ${y(p.h)} V ${y(p.l)}" stroke="${color}" stroke-width="1"/><rect x="${x(i)-2.8}" y="${Math.min(y(p.o),y(p.c))}" width="5.6" height="${Math.max(1.5, Math.abs(y(p.o)-y(p.c)))}" fill="${color}"/><rect x="${x(i)-2.8}" y="${305-(12+(i%9)*2+Math.abs(p.c-p.o)*2)}" width="5.6" height="${12+(i%9)*2+Math.abs(p.c-p.o)*2}" fill="${color}" fill-opacity=".26"/>`; });
    [5,20].forEach((period, j) => { let ema = points[0].c; const line = points.map((p,i) => {ema += (p.c-ema)*2/(period+1);return `${i?'L':'M'}${x(i)},${y(ema)}`;}).join(' ');svg+=`<path d="${line}" fill="none" stroke="${j?'#77a3cc':'#c0cbd2'}" stroke-width="1" opacity=".65"/>`; });
    if ($('show-bi').checked) {const indices=[0,13,23,29,39,59,67,76];svg+=`<path d="${indices.map((i,j)=>`${j?'L':'M'}${x(i)},${y(j%2?points[i].h:points[i].l)}`).join(' ')}" fill="none" stroke="#c5a373" stroke-width="1.4"/>`;}
    const last=points[77].c; svg+=`<path d="M ${left} ${y(last)} H ${right}" stroke="#87caa7" stroke-opacity=".6" stroke-dasharray="3 5"/><rect x="${right+5}" y="${y(last)-10}" width="72" height="19" rx="2" fill="#294c3b"/><text x="${right+10}" y="${y(last)+3}" fill="#a3e4bd" font-size="10" font-family="monospace">${price(last)}</text>`;
    if ($('show-signals').checked) {[[23,'B1',false],[67,Number($('timeline').value)>=5?'B2 · 锁定':'B2? · 待锁定',true]].filter((item,index)=>index===0||Number($('timeline').value)>=2).forEach(([i,label,active])=>{const yy=y(points[i].l)+15;svg+=`<path d="M ${x(i)-3} ${yy-4} l 3 -5 l 3 5" fill="#8cdfb0"/><rect x="${x(i)-13}" y="${yy}" width="${active?85:30}" height="19" rx="3" fill="#233d31"/><text x="${x(i)-7}" y="${yy+13}" font-size="10" fill="#98d8b3">${label}</text>`;});}
    svg += '<text x="20" y="283" font-size="9" fill="#536f80">VOL · 合成样例</text></svg>';
    $('market-chart').innerHTML=svg;
    $('quote').textContent=price(3142.6); $('entry').textContent=price(3142.6); $('stop').textContent=price(3106); $('target').textContent=price(3241.5);
  }
  function renderTable() {
    const coin = state.coin + '-USDT';
    let headers, rows, note;
    if(state.table==='signals') {
      headers=['首次观察 / UTC','品种','信号','状态','决策原因'];
      rows=[['10:30',coin,'<span class="green">B2</span>','<span class="pill warn">待锁定</span>','等待下一笔确认'],['09:15',coin,'<span class="green">B3</span>','<span class="pill neutral">已过滤</span>','净盈亏比不足 1 : 2'],['08:45','BTC-USDT','<span class="red-text">S2</span>','<span class="pill neutral">已过滤</span>','与 4H 方向冲突'],['07:30',coin,'<span class="green">B2</span>','<span class="pill">候选记录</span>','锁定、结构与方向通过']];
      note='样例：每个信号保留当时的观察状态，不回填未来确认结果。';
    } else if(state.table==='positions') {
      headers=['品种 / 方向','样例数量','入场均价','止损保护','操作'];
      rows=[[coin+' <span class="green">多</span>',state.coin==='ETH'?'0.50 ETH':'0.025 BTC',price(3128),'<span class="pill">保护已生效 · 样例</span>','<button id="close-demo" class="small-tag" style="background:#292125;color:#e7a7a7">演示平仓</button>']];
      note='这是持仓布局样例，未查询欧易账户。演示平仓只清空本页样例。';
    } else {
      headers=['时间 / UTC','订单用途','品种','成交 / 委托','状态'];
      rows=[['07:32','止损保护',coin,'—','<span class="pill">等待触发</span>'],['07:31','开仓 · 限价',coin,state.coin==='ETH'?'0.50 / 0.50':'0.025 / 0.025','<span class="pill">全部成交</span>'],['07:30','开仓 · 限价',coin,state.coin==='ETH'?'0 / 0.50':'0 / 0.025','<span class="pill neutral">超时撤单</span>']];
      note='样例：受理不等于成交，撤单后仍核对累计成交数量与保护状态。';
    }
    $('activity-table').innerHTML='<table><thead><tr>'+headers.map(s=>`<th>${s}</th>`).join('')+'</tr></thead><tbody>'+rows.map(row=>'<tr>'+row.map(s=>`<td>${s}</td>`).join('')+'</tr>').join('')+'</tbody></table>';
    $('activity-footer').textContent=note;
    if($('close-demo')) $('close-demo').onclick=()=>{ $('activity-table').innerHTML='<div style="padding:42px 24px;text-align:center"><div class="green" style="font-size:16px">本页样例持仓已清空</div><p class="muted">未向欧易发送任何指令。重新切换此页签可重置演示。</p></div>'; };
  }
  function timeline() {
    const n=Number($('timeline').value), locked=n>=5, observed=n>=2, enabled=$('allow-entry').checked;
    $('replay-time').textContent=time(n);
    $('decision-label').innerHTML='<span class="decision-dot"></span>'+(locked&&enabled?'候选做多':'观望');
    $('decision-label').style.color=locked&&enabled?'var(--green)':'var(--amber)';
    $('decision-title').textContent=!enabled?'已暂停新增仓位':locked?'B2 锁定，进入风控检查':observed?'等待 B2 信号锁定':'等待回调信号出现';
    $('decision-copy').textContent=locked?'样例结构条件已通过。实际执行仍需核对账户风险、盘口价格和保护单。':'方向与结构位置已满足。下一笔确认后，才进入风险与下单检查。';
    $('lock-icon').textContent=locked?'✓':'◷'; $('lock-icon').className=locked?'check':'pending';
    $('lock-text').textContent=locked?'已锁定':observed?'待确认':'未出现'; $('lock-text').className=locked?'green':'amber';
    $('timeline-caption').textContent=`${time(n)} · `+(locked?'样例信号现已锁定，只能从此后考虑执行，不能回到 10:00 的极值价成交。':observed?'B2 已出现但尚未锁定，继续观察。图上极值时间不等于入场时间。':'极值已经出现，但当时还不知道这里会形成买点。');
    $('decision-note').textContent=!enabled?'新增仓位已暂停，已有持仓的保护继续保留。':locked?'仅演示候选状态；此页面没有下单执行器。':'未锁定信号不入场，评分不能覆盖硬性规则。';
    chart();
  }
  function stopPlayback() {if(state.timer) clearInterval(state.timer);state.timer=null;$('play').textContent=state.mode==='research'?'▶ 开始回放演示':'▶ 演示信号检查';}
  document.querySelectorAll('[data-section]').forEach(button=>button.onclick=()=>{
    document.querySelectorAll('[data-section]').forEach(b=>b.classList.toggle('active',b===button));
    const section=$(button.dataset.section);section.scrollIntoView({block:'start',behavior:matchMedia('(prefers-reduced-motion: reduce)').matches?'instant':'smooth'});section.classList.remove('nav-flash');void section.offsetWidth;section.classList.add('nav-flash');
  });
  document.querySelectorAll('[data-mode]').forEach(button=>button.onclick=()=>{
    state.mode=button.dataset.mode;stopPlayback();
    document.querySelectorAll('[data-mode]').forEach(b=>{b.classList.toggle('selected',b===button);b.setAttribute('aria-pressed',String(b===button));});
    $('stat-label').textContent=state.mode==='research'?'样例研究区间':'样例账户权益 / USDT';
    $('stat-value').innerHTML=state.mode==='research'?'08.01 <span>—</span> 08.31':'10,000<small>.00</small>';
    $('stat-foot').textContent=state.mode==='research'?'2026 年 · 15m 收盘事件':'演示账户金额 · 未连接欧易';
    $('run-status').textContent=state.mode==='research'?'演示待启动':'模拟盘布局预览';
  });
  document.querySelectorAll('[data-period]').forEach(button=>button.onclick=()=>{state.period=button.dataset.period;document.querySelectorAll('[data-period]').forEach(b=>b.classList.toggle('selected',b===button));chart();});
  document.querySelectorAll('[data-table]').forEach(button=>button.onclick=()=>{state.table=button.dataset.table;document.querySelectorAll('[data-table]').forEach(b=>{b.classList.toggle('selected',b===button);b.setAttribute('aria-selected',String(b===button));});renderTable();});
  $('instrument').onchange=()=>{state.coin=$('instrument').value;$('coin-icon').textContent=state.coin==='ETH'?'Ξ':'₿';chart();renderTable();};
  ['show-bi','show-zs','show-signals'].forEach(id=>$(id).onchange=chart);
  $('timeline').oninput=()=>{stopPlayback();$('run-status').textContent='手动回放演示';timeline();};
  $('play').onclick=()=>{if(state.timer){stopPlayback();$('run-status').textContent='回放演示已暂停';return;}$('timeline').value=0;timeline();$('play').textContent='Ⅱ 暂停演示';$('run-status').textContent='正在回放演示';state.timer=setInterval(()=>{$('timeline').value=Number($('timeline').value)+1;timeline();if(Number($('timeline').value)>=8){stopPlayback();$('run-status').textContent='本轮演示完成';}},800);};
  $('allow-entry').onchange=()=>{$('pause-note').textContent=$('allow-entry').checked?'开启后仍需通过所有风控检查':'已暂停新增仓位 · 持仓保护保留';timeline();};
  $('risk-input').oninput=()=>{const n=Number($('risk-input').value);if(!$('risk-input').value||!Number.isFinite(n)||n<.1||n>1){$('risk-feedback').textContent='演示范围为 0.1%–1.0%，请输入有效值。';$('risk-input').setAttribute('aria-invalid','true');return;}$('risk-input').removeAttribute('aria-invalid');$('budget-value').textContent=(10000*n/100).toFixed(2);$('risk-stat').innerHTML=n.toFixed(2)+'<small>%</small>';$('risk-feedback').textContent='已更新本页预算演示，未保存到后台策略配置。';};
  window.addEventListener('pagehide',stopPlayback);
  timeline();renderTable();
})();
