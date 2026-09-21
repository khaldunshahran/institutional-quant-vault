// Polymarket BTC 5M Live Trading Cockpit Logic

let backtestData = null;

let chartInstance = null;

let activeChartFilter = 'all'; // 'all' or 'capped'

let selectedProfile = 'momentum_value';

let selectedStake = 5.0;

let selectedThreshold = 0.70;

let selectedRunMode = 'one_shot'; // 'one_shot' or 'continuous_loop'

let liveSecondsLeft = 0;

let currentMarketCloseAtTs = 0; // Epoch seconds for drift-free countdown

let currentLedgerMode = 'paper'; // 'paper' or 'live'

let sparklineTicks = [];

let activeLogFilter = 'all';

let rawLogsCache = '';

let lastTelemetrySuccess = Date.now();

let isTabVisible = true;

let currentBotRunning = false;

let currentWalletUsdcBalance = 0.0;

let activeMaxTradesPerDay = 16;



// Cached DOM references to eliminate repeated getElementById lookups

const DOM = {};



function initDOM() {

  const ids = [

    'walletAddressDisplay', 'walletUsdcBalance', 'authStatusPill',

    'botStatusBadge', 'botStatusText', 'headerMarketPill', 'marketPulse',

    'currentSlugDisplay', 'countdownSeconds', 'liveFeedBadge',

    'liveBtcSpot', 'liveBtcOpen', 'liveBtcImpulse',

    'impulseGaugeFill', 'impulseGaugePct', 'signalAlertBanner',

    'signalAlertIcon', 'signalAlertTitle', 'signalAlertDesc',

    'candlePhasePill', 'feedStaleAlert', 'feedStaleText',

    'botErrorBanner', 'botErrorText', 'readinessStrip',

    'rsValPhase', 'rsValImpulse', 'rsValSignal',

    'obUpProb', 'obUpBid', 'obUpAsk', 'obUpSpread',

    'obDownProb', 'obDownBid', 'obDownAsk', 'obDownSpread',

    'skewUpLabel', 'skewDownLabel', 'skewFillUp', 'skewFillDown',

    'linkPolymarket', 'mktTitleShort',

    'btnStratMomentum', 'btnStratScalp', 'btnStratReversion', 'btnStratHedge', 'btnStratSniper', 'strategySpecHint',

    'customStakeInput', 'modeOneShot', 'modeLoop',

    'chkExecuteLive', 'executeLabel', 'consoleModePill',

    'stakeBalanceWarning', 'btnStartBot', 'btnStopBot',

    'stateRunning', 'statePid', 'stateMode', 'stateRuntime',

    'ledgerModeBadge', 'ledgerSubTitle', 'btnLedgerPaper', 'btnLedgerLive',

    'lmTotalTrades', 'lmWinRate', 'lmWinsLosses', 'lmNetPnl', 'lmAvgPnl', 'lmProfitFactor',

    'tradeLedgerTable', 'tradeLedgerBody',

    'liveSafetyModal', 'liveConfirmInput', 'btnConfirmLive',

    'modalWalletAddr', 'modalWalletUsdc', 'modalStakeAmt',

    'modalTradesCount', 'modalTotalExposure',

    'leaderboardTable', 'leaderboardBody', 'btnRunMultiBacktest',

    'equityChart', 'tableBody', 'tableSearch', 'filterTradeStatus',

    'terminalOutput', 'logFileHeader', 'chkAutoScroll',

    'cfg_mv_threshold', 'cfg_mv_max_entry', 'cfg_mv_stake', 'cfg_mv_tp', 'cfg_mv_sl', 'cfg_mv_impulse', 'cfg_mv_cap',

    'cfg_qs_threshold', 'cfg_qs_max_entry', 'cfg_qs_stake', 'cfg_qs_tp', 'cfg_qs_trailing', 'cfg_qs_sl', 'cfg_qs_impulse', 'cfg_qs_cap',

    'cfg_mr_fav_trigger', 'cfg_mr_max_entry', 'cfg_mr_stake', 'cfg_mr_tp', 'cfg_mr_sl', 'cfg_mr_cap',

    'cfg_sh_threshold', 'cfg_sh_max_entry', 'cfg_sh_stake', 'cfg_sh_sl', 'cfg_sh_cap',

    'cfg_ts_threshold', 'cfg_ts_max_entry', 'cfg_ts_stake', 'cfg_ts_tp', 'cfg_ts_sl', 'cfg_ts_impulse', 'cfg_ts_cap',

    'settingsStatusMsg', 'sparklineTrend', 'btcSparkline'

  ];

  ids.forEach(id => {

    DOM[id] = document.getElementById(id);

  });

}



document.addEventListener('DOMContentLoaded', () => {

  initDOM();

  initTabs();

  initExecuteSwitch();

  initEmergencyHotkeys();

  initPageVisibility();

  loadBacktestData();

  loadStrategyComparison();

  loadActiveSettings();

  

  // Fast telemetry polling every 1.5 seconds (paused when tab hidden)

  fetchTelemetry();

  setInterval(() => {

    if (isTabVisible) fetchTelemetry();

  }, 1500);



  // Status & historical trades polling every 3 seconds

  fetchStatus();

  fetchTrades(currentLedgerMode);

  setInterval(() => {

    if (isTabVisible) {

      fetchStatus();

      fetchTrades(currentLedgerMode);

    }

  }, 3000);



  // Drift-free self-correcting 1-second ticker + feed staleness watchdog

  setInterval(() => {

    if (currentMarketCloseAtTs > 0) {

      const nowSec = Date.now() / 1000;

      const secLeft = Math.max(0, Math.floor(currentMarketCloseAtTs - nowSec));

      liveSecondsLeft = secLeft;

      updateCountdownDisplay(secLeft);

    }

    checkFeedStaleness();

  }, 1000);



  // Logs polling every 3.5 seconds

  setInterval(() => {

    if (!isTabVisible) return;

    const logsTab = document.getElementById('tab-logs');

    if (logsTab && logsTab.classList.contains('active')) {

      fetchLogs();

    }

  }, 3500);

});



/* ================= PAGE VISIBILITY & HOTKEYS ================= */

function initPageVisibility() {

  document.addEventListener('visibilitychange', () => {

    isTabVisible = !document.hidden;

    if (isTabVisible) {

      fetchTelemetry();

      fetchStatus();

      fetchTrades(currentLedgerMode);

    }

  });

}



function initEmergencyHotkeys() {

  window.addEventListener('keydown', (e) => {

    if (e.key === 'Escape' || (e.ctrlKey && e.key.toLowerCase() === 'q')) {

      if (currentBotRunning) {

        e.preventDefault();

        stopBot();

      }

    }

  });

}



function checkFeedStaleness() {

  const diff = Date.now() - lastTelemetrySuccess;

  if (diff > 4000) {

    if (DOM.feedStaleAlert) {

      DOM.feedStaleAlert.style.display = 'flex';

      DOM.feedStaleText.textContent = `⚠️ Telemetry feed delayed (${(diff/1000).toFixed(1)}s ago). Live prices may be stale. Reconnecting...`;

    }

    if (DOM.liveFeedBadge) {

      DOM.liveFeedBadge.className = 'badge badge-rose';

      DOM.liveFeedBadge.textContent = 'Feed: Delayed';

    }

  } else {

    if (DOM.feedStaleAlert) DOM.feedStaleAlert.style.display = 'none';

    if (DOM.liveFeedBadge) {

      DOM.liveFeedBadge.className = 'badge badge-cyan';

      DOM.liveFeedBadge.textContent = 'Feed: Connected';

    }

  }

}



function showBotError(msg) {

  if (DOM.botErrorBanner && DOM.botErrorText) {

    DOM.botErrorText.textContent = msg;

    DOM.botErrorBanner.style.display = 'flex';

  } else {

    console.error(msg);

  }

}



function dismissBotError() {

  if (DOM.botErrorBanner) {

    DOM.botErrorBanner.style.display = 'none';

  }

}



/* ================= TAB NAVIGATION ================= */

function switchTab(tabId) {

  document.querySelectorAll('.tab-btn').forEach(btn => {

    const isTarget = (btn.dataset.tab === tabId);

    btn.classList.toggle('active', isTarget);

    btn.classList.toggle('active-highlight', isTarget && (tabId === 'long-short'));

  });

  document.querySelectorAll('.tab-pane').forEach(pane => {

    pane.classList.remove('active');

  });

  const activePane = document.getElementById(`tab-${tabId}`);

  if (activePane) {

    activePane.classList.add('active');

  }

  // Ensure scroll is reset to top so screener and other tabs never have blank space
  window.scrollTo({ top: 0, behavior: 'instant' });

  if (tabId === 'long-short') {

    if (typeof startLongShortPolling === 'function') startLongShortPolling();

    if (typeof fetchJevLongShortTelemetry === 'function') fetchJevLongShortTelemetry();

    if (typeof fetchCandleData === 'function') fetchCandleData();

  } else if (tabId === 'screener') {

    if (typeof fetchBinanceUniverse === 'function') fetchBinanceUniverse(true);

  } else if (tabId === 'cockpit') {

    if (typeof fetchTelemetry === 'function') fetchTelemetry();

  } else if (tabId === 'settings') {

    if (typeof loadSettings === 'function') loadSettings();

  }

}

function switchSettingsSubTab(subId) {

  document.querySelectorAll('.settings-sub-tab').forEach(b => {

    const isTarget = (b.dataset.subtab === subId);

    b.classList.toggle('active', isTarget);

    b.style.background = isTarget ? '#4f46e5' : 'transparent';

    b.style.color = isTarget ? '#fff' : 'var(--text-secondary)';

  });

  ['config', 'backtest', 'logs'].forEach(id => {

    const el = document.getElementById(`subtab-${id}`);

    if (el) el.style.display = (id === subId) ? 'block' : 'none';

  });

  if (subId === 'logs' && typeof fetchLogs === 'function') fetchLogs();

  if (subId === 'backtest' && typeof loadBacktestData === 'function' && !backtestData) loadBacktestData();

}

function initTabs() {

  document.querySelectorAll('.tab-btn').forEach(btn => {

    btn.addEventListener('click', () => {

      switchTab(btn.dataset.tab);

    });

  });

}



/* ================= LIVE TELEMETRY (BTC SPOT, IMPULSE, ORDERBOOK, WALLET) ================= */

async function fetchTelemetry() {

  try {

    const res = await fetch('/api/telemetry');

    if (!res.ok) throw new Error(`HTTP ${res.status}`);

    const data = await res.json();

    lastTelemetrySuccess = Date.now();

    renderTelemetry(data);

  } catch (e) {

    // Staleness watchdog handles feed delay notification

  }

}



function renderTelemetry(data) {

  // 1. Wallet Telemetry

  const wallet = data.wallet || {};

  if (wallet.address && DOM.walletAddressDisplay) {

    const shortAddr = wallet.address.length > 10 ? `${wallet.address.slice(0, 6)}...${wallet.address.slice(-4)}` : wallet.address;

    DOM.walletAddressDisplay.textContent = shortAddr;

    DOM.walletAddressDisplay.onclick = () => window.open(`https://polygonscan.com/address/${wallet.address}`, '_blank');

  }

  if (wallet.usdc_balance !== undefined) {

    currentWalletUsdcBalance = parseFloat(wallet.usdc_balance) || 0.0;

    if (DOM.walletUsdcBalance) DOM.walletUsdcBalance.textContent = `$${wallet.usdc_balance}`;

  }

  if (wallet.auth_verified && DOM.authStatusPill) {

    DOM.authStatusPill.className = 'auth-pill verified';

    DOM.authStatusPill.innerHTML = '<span class="auth-dot"></span><span>L2 AUTH VERIFIED</span>';

  }



  // 2. BTC Spot Price & 5M Open

  if (data.btc_spot !== null && data.btc_spot !== undefined) {

    if (DOM.liveBtcSpot) DOM.liveBtcSpot.textContent = `$${data.btc_spot.toLocaleString(undefined, {minimumFractionDigits: 2, maximumFractionDigits: 2})}`;

    updateSparkline(data.btc_spot);

  }

  if (data.btc_open !== null && data.btc_open !== undefined && DOM.liveBtcOpen) {

    DOM.liveBtcOpen.textContent = `$${data.btc_open.toLocaleString(undefined, {minimumFractionDigits: 2, maximumFractionDigits: 2})}`;

  }



  // 3. 5M Net Impulse & Visual Gauge

  const impulse = data.btc_impulse;

  const absImp = impulse !== null && impulse !== undefined ? Math.abs(impulse) : 0;

  const isUp = impulse !== null && impulse !== undefined ? impulse >= 0 : true;

  const sign = isUp ? '+' : '';



  if (impulse !== null && impulse !== undefined && DOM.liveBtcImpulse) {

    DOM.liveBtcImpulse.textContent = `${sign}$${impulse.toFixed(2)} USD`;

    DOM.liveBtcImpulse.style.color = isUp ? 'var(--emerald)' : 'var(--rose)';



    const progress = Math.min(100, Math.round((absImp / 70.0) * 100));

    if (DOM.impulseGaugeFill) {

      DOM.impulseGaugeFill.style.width = `${progress}%`;

      DOM.impulseGaugeFill.style.background = isUp 

        ? 'linear-gradient(90deg, #34d399, var(--emerald))'

        : 'linear-gradient(90deg, #c084fc, var(--purple))';

    }

    if (DOM.impulseGaugePct) {

      DOM.impulseGaugePct.textContent = `${progress}% of $70 threshold (${sign}$${impulse.toFixed(1)} / $70)`;

    }

  }



  // 4. Candle Phase & Drift-Free Epoch Countdown

  const mkt = data.market || {};

  if (mkt.close_at_ts) {

    currentMarketCloseAtTs = mkt.close_at_ts;

    const nowSec = Date.now() / 1000;

    liveSecondsLeft = Math.max(0, Math.floor(currentMarketCloseAtTs - nowSec));

  } else {

    liveSecondsLeft = mkt.seconds_left || 0;

  }

  updateCountdownDisplay(liveSecondsLeft);



  let phaseText = 'Phase 1: Formation';

  let phaseColor = 'var(--text-primary)';

  let signalReadiness = 'WAITING FOR ENTRY WINDOW';

  let signalColor = 'var(--text-muted)';



  if (liveSecondsLeft > 150) {

    phaseText = 'Phase 1: Formation';

  } else if (liveSecondsLeft <= 150 && liveSecondsLeft >= 60) {

    phaseText = 'Phase 2: Target Entry Window (~120s)';

    phaseColor = 'var(--emerald)';

    if (absImp >= 70.0) {

      signalReadiness = `🟢 ARMED FOR ${isUp ? 'UP' : 'DOWN'} (Fires @ 120s)`;

      signalColor = 'var(--emerald)';

    } else {

      signalReadiness = 'MONITORING IMPULSE FORMATION';

      signalColor = 'var(--cyan)';

    }

  } else if (liveSecondsLeft < 60 && liveSecondsLeft >= 20) {

    phaseText = 'Phase 3: Position Monitoring';

    phaseColor = 'var(--cyan)';

    signalReadiness = 'POSITION ACTIVE / MONITORING';

    signalColor = 'var(--cyan)';

  } else {

    phaseText = 'Phase 4: Pre-Close Exit / Settlement';

    phaseColor = 'var(--amber)';

    signalReadiness = 'PRE-CLOSE LIQUIDATION';

    signalColor = 'var(--amber)';

  }



  if (DOM.candlePhasePill) {

    DOM.candlePhasePill.textContent = phaseText;

    DOM.candlePhasePill.style.color = phaseColor;

    DOM.candlePhasePill.style.borderColor = phaseColor;

  }



  // Update Compact 3-Step Readiness Strip

  if (DOM.rsValPhase) DOM.rsValPhase.textContent = `${phaseText} (${liveSecondsLeft}s left)`;

  if (DOM.rsValImpulse) {

    const prog = Math.min(100, Math.round((absImp / 70.0) * 100));

    DOM.rsValImpulse.textContent = `${sign}$${(impulse || 0).toFixed(1)} / $70 (${prog}%)`;

    DOM.rsValImpulse.style.color = isUp ? 'var(--emerald)' : 'var(--rose)';

  }

  if (DOM.rsValSignal) {

    DOM.rsValSignal.textContent = signalReadiness;

    DOM.rsValSignal.style.color = signalColor;

  }



  // Check if threshold reached for main alert banner

  if (DOM.signalAlertBanner) {

    if (absImp >= 70.0) {

      DOM.signalAlertBanner.className = 'signal-alert-banner triggered';

      if (DOM.signalAlertIcon) DOM.signalAlertIcon.textContent = isUp ? '🚀' : '🔻';

      if (DOM.signalAlertTitle) DOM.signalAlertTitle.textContent = `STRONG ${isUp ? 'BULLISH' : 'BEARISH'} MOMENTUM (+${absImp.toFixed(1)} USD)`;

      if (DOM.signalAlertDesc) DOM.signalAlertDesc.textContent = `BTC impulse exceeded $70 threshold. Strategy triggers ${isUp ? 'UP' : 'DOWN'} outcome entry.`;

    } else if (liveSecondsLeft <= 150 && liveSecondsLeft >= 60) {

      DOM.signalAlertBanner.className = 'signal-alert-banner entry-window';

      if (DOM.signalAlertIcon) DOM.signalAlertIcon.textContent = '🎯';

      if (DOM.signalAlertTitle) DOM.signalAlertTitle.textContent = 'TARGET ENTRY WINDOW ACTIVE';

      if (DOM.signalAlertDesc) DOM.signalAlertDesc.textContent = 'Ready to place order if impulse satisfies $70 move requirement.';

    } else {

      DOM.signalAlertBanner.className = 'signal-alert-banner idle';

      if (DOM.signalAlertIcon) DOM.signalAlertIcon.textContent = '⏳';

      if (DOM.signalAlertTitle) DOM.signalAlertTitle.textContent = 'MONITORING BTC 5-MINUTE CANDLE';

      if (DOM.signalAlertDesc) DOM.signalAlertDesc.textContent = 'Waiting for candle momentum to form. Target entry window triggers around 120s remaining.';

    }

  }



  // 5. Active Market Details & Header Pill

  if (mkt.slug && DOM.currentSlugDisplay) {

    DOM.currentSlugDisplay.textContent = mkt.slug;

  }

  if (mkt.title && DOM.mktTitleShort) {

    DOM.mktTitleShort.textContent = mkt.title;

  }

  if (mkt.slug && DOM.linkPolymarket) {

    DOM.linkPolymarket.href = `https://polymarket.com/market/${mkt.slug}`;

  }



  // 6. Polymarket CLOB Orderbook Display (Harmonized Emerald for UP, Purple for DOWN)

  const up = mkt.clob_up || {};

  const dn = mkt.clob_down || {};

  if (DOM.obUpBid) DOM.obUpBid.textContent = up.bid ? `$${up.bid.toFixed(2)}` : 'N/A';

  if (DOM.obUpAsk) DOM.obUpAsk.textContent = up.ask ? `$${up.ask.toFixed(2)}` : 'N/A';

  if (DOM.obUpSpread) DOM.obUpSpread.textContent = up.spread ? `$${up.spread.toFixed(2)}` : '—';

  if (DOM.obUpProb) DOM.obUpProb.textContent = `${mkt.skew_up_pct || 50}%`;



  if (DOM.obDownBid) DOM.obDownBid.textContent = dn.bid ? `$${dn.bid.toFixed(2)}` : 'N/A';

  if (DOM.obDownAsk) DOM.obDownAsk.textContent = dn.ask ? `$${dn.ask.toFixed(2)}` : 'N/A';

  if (DOM.obDownSpread) DOM.obDownSpread.textContent = dn.spread ? `$${dn.spread.toFixed(2)}` : '—';

  if (DOM.obDownProb) DOM.obDownProb.textContent = `${mkt.skew_down_pct || 50}%`;



  // Skew Bar

  const skewUp = mkt.skew_up_pct || 50;

  const skewDn = mkt.skew_down_pct || 50;

  if (DOM.skewUpLabel) DOM.skewUpLabel.textContent = `${skewUp}%`;

  if (DOM.skewDownLabel) DOM.skewDownLabel.textContent = `${skewDn}%`;

  if (DOM.skewFillUp) DOM.skewFillUp.style.width = `${skewUp}%`;

  if (DOM.skewFillDown) DOM.skewFillDown.style.width = `${skewDn}%`;



  // 7. Verify Stake vs Balance Guard

  checkStakeBalanceSafety();

}



function updateCountdownDisplay(sec) {

  if (DOM.countdownSeconds) {

    DOM.countdownSeconds.textContent = sec;

  }

}



/* ================= BOT EXECUTION CONTROLS ================= */

const STRATEGY_SPECS = {

  jev_ai_brain: {

    name: 'Jev AI Brain',

    hint: 'TypeSafe System One | Choice + Score + Noul | Conf >= 0.65 | Conviction >= 3.0',

    pillId: 'btnStratJev',

    defaultCap: 20

  },

  momentum_value: {

    name: 'Momentum Value',

    hint: 'Ceiling $0.78 | TP +22% | SL -25% | Impulse ±$65',

    pillId: 'btnStratMomentum',

    defaultCap: 16

  },

  quick_scalp: {

    name: 'Quick Scalp',

    hint: 'Ceiling $0.80 | TP +12% | Trail BE +8% | SL -18% | Impulse ±$55',

    pillId: 'btnStratScalp',

    defaultCap: 24

  },

  mean_reversion: {

    name: 'Mean Reversion',

    hint: 'Fade Fav ≥0.85 | Underdog ≤$0.25 | TP +150% | SL -50%',

    pillId: 'btnStratReversion',

    defaultCap: 12

  },

  skew_hedge: {

    name: 'Skew Hedge',

    hint: 'Ceiling $0.82 | Core +25% | 5% Tail Hedge at Skew ≥0.93',

    pillId: 'btnStratHedge',

    defaultCap: 12

  },

  macro_trend_sniper: {

    name: 'Trend Sniper',

    hint: 'Ceiling $0.82 | TP +28% | SL -20% | High Impulse ±$85',

    pillId: 'btnStratSniper',

    defaultCap: 10

  },

  conservative: {

    name: 'Conservative',

    hint: 'Legacy Profile: Ask ≥0.70 | SL -25% | Cap 12',

    pillId: 'btnStratMomentum',

    defaultCap: 12

  },

  aggressive: {

    name: 'Aggressive',

    hint: 'Legacy Profile: Ask ≥0.70 | SL -30% | Cap 20',

    pillId: 'btnStratScalp',

    defaultCap: 20

  }

};



function setStrategyProfile(stratId) {

  selectedProfile = stratId;

  const spec = STRATEGY_SPECS[stratId] || STRATEGY_SPECS.momentum_value;



  ['btnStratJev', 'btnStratMomentum', 'btnStratScalp', 'btnStratReversion', 'btnStratHedge', 'btnStratSniper'].forEach(id => {

    const el = document.getElementById(id);

    if (el) el.classList.toggle('active', id === spec.pillId);

  });



  const hintEl = DOM.strategySpecHint || document.getElementById('strategySpecHint');

  if (hintEl) hintEl.textContent = spec.hint;



  activeMaxTradesPerDay = spec.defaultCap;

  checkStakeBalanceSafety();

  updateLeaderboardDeployButtons(stratId);

}



function setProfile(profile) {

  setStrategyProfile(profile);

}



function deployStrategy(stratId) {

  setStrategyProfile(stratId);

  switchTab('cockpit');

  const deck = document.querySelector('.control-deck-card');

  if (deck) {

    deck.scrollIntoView({ behavior: 'smooth', block: 'center' });

    deck.style.boxShadow = '0 0 25px rgba(6, 182, 212, 0.5)';

    setTimeout(() => { deck.style.boxShadow = ''; }, 1800);

  }

}



function updateLeaderboardDeployButtons(activeStratId) {

  document.querySelectorAll('.btn-deploy-strat').forEach(btn => {

    const sId = btn.getAttribute('data-strat-id');

    if (sId === activeStratId) {

      btn.textContent = '✓ Active in Bot';

      btn.classList.add('deployed');

    } else {

      btn.textContent = 'Deploy Strategy';

      btn.classList.remove('deployed');

    }

  });

}



function setStake(amount) {

  selectedStake = amount;

  document.querySelectorAll('.stake-btn').forEach(btn => {

    btn.classList.toggle('active', btn.textContent === `$${amount}`);

  });

  if (DOM.customStakeInput) DOM.customStakeInput.value = '';

  checkStakeBalanceSafety();

}



function setCustomStake(val) {

  const num = parseFloat(val);

  if (!isNaN(num) && num > 0) {

    selectedStake = num;

    document.querySelectorAll('.stake-btn').forEach(b => b.classList.remove('active'));

    checkStakeBalanceSafety();

  }

}



function setThreshold(th) {

  selectedThreshold = th;

  document.querySelectorAll('.form-group:nth-child(3) .stake-btn').forEach(btn => {

    btn.classList.toggle('active', parseFloat(btn.textContent) === th);

  });

}



function setRunMode(mode) {

  selectedRunMode = mode;

  if (DOM.modeOneShot) DOM.modeOneShot.classList.toggle('active', mode === 'one_shot');

  if (DOM.modeLoop) DOM.modeLoop.classList.toggle('active', mode === 'continuous_loop');

}



function checkStakeBalanceSafety() {

  const isLive = DOM.chkExecuteLive ? DOM.chkExecuteLive.checked : false;

  const hasWarning = isLive && (selectedStake > currentWalletUsdcBalance);

  if (DOM.stakeBalanceWarning) {

    if (hasWarning) {

      DOM.stakeBalanceWarning.style.display = 'block';

      DOM.stakeBalanceWarning.textContent = `⚠️ Target stake ($${selectedStake.toFixed(2)}) exceeds wallet balance ($${currentWalletUsdcBalance.toFixed(2)}). Real order execution disabled until deposit.`;

    } else {

      DOM.stakeBalanceWarning.style.display = 'none';

    }

  }

  if (DOM.btnStartBot && !currentBotRunning) {

    if (hasWarning) {

      DOM.btnStartBot.disabled = true;

      DOM.btnStartBot.classList.add('btn-disabled');

      DOM.btnStartBot.title = 'Insufficient USDC balance for live order placement';

    } else {

      DOM.btnStartBot.disabled = false;

      DOM.btnStartBot.classList.remove('btn-disabled');

      DOM.btnStartBot.title = '';

    }

  }

}



function initExecuteSwitch() {

  const chk = DOM.chkExecuteLive || document.getElementById('chkExecuteLive');

  if (!chk) return;



  chk.addEventListener('change', () => {

    if (chk.checked) {

      showLiveSafetyModal();

    } else {

      applyModeChange(false);

    }

  });

}



function handleLiveConfirmInput(val) {

  const isMatch = (val || '').trim().toUpperCase() === 'LIVE';

  if (DOM.btnConfirmLive) {

    DOM.btnConfirmLive.disabled = !isMatch;

    DOM.btnConfirmLive.classList.toggle('btn-disabled', !isMatch);

  }

}



function showLiveSafetyModal() {

  const modal = DOM.liveSafetyModal || document.getElementById('liveSafetyModal');

  if (!modal) return;

  const walletAddr = DOM.walletAddressDisplay ? DOM.walletAddressDisplay.textContent : '0xC092...05ad';

  const walletBal = DOM.walletUsdcBalance ? DOM.walletUsdcBalance.textContent : '$0.00';

  if (DOM.modalWalletAddr) DOM.modalWalletAddr.textContent = walletAddr;

  if (DOM.modalWalletUsdc) DOM.modalWalletUsdc.textContent = `${walletBal} USDC`;

  if (DOM.modalStakeAmt) DOM.modalStakeAmt.textContent = `$${selectedStake.toFixed(2)} USDC`;

  if (DOM.modalTradesCount) DOM.modalTradesCount.textContent = activeMaxTradesPerDay;



  const totalExposure = selectedStake * activeMaxTradesPerDay;

  if (DOM.modalTotalExposure) DOM.modalTotalExposure.textContent = `$${totalExposure.toFixed(2)} USDC`;



  if (DOM.liveConfirmInput) DOM.liveConfirmInput.value = '';

  if (DOM.btnConfirmLive) {

    DOM.btnConfirmLive.disabled = true;

    DOM.btnConfirmLive.classList.add('btn-disabled');

  }



  modal.style.display = 'flex';

  setTimeout(() => {

    if (DOM.liveConfirmInput) DOM.liveConfirmInput.focus();

  }, 60);

}



function dismissLiveSafetyModal(confirmed) {

  const modal = DOM.liveSafetyModal || document.getElementById('liveSafetyModal');

  if (modal) modal.style.display = 'none';

  if (DOM.liveConfirmInput) DOM.liveConfirmInput.value = '';



  const chk = DOM.chkExecuteLive || document.getElementById('chkExecuteLive');

  if (chk) {

    if (confirmed) {

      chk.checked = true;

      applyModeChange(true);

    } else {

      chk.checked = false;

      applyModeChange(false);

    }

  }

}



function applyModeChange(isLive) {

  const label = DOM.executeLabel || document.getElementById('executeLabel');

  const pill = DOM.consoleModePill || document.getElementById('consoleModePill');

  if (isLive) {

    if (label) {

      label.textContent = 'LIVE EXECUTION MODE (REAL FUNDS COMMITTED)';

      label.style.color = 'var(--rose)';

    }

    if (pill) {

      pill.textContent = 'Mode: LIVE REAL ORDERS';

      pill.className = 'badge badge-danger';

    }

    switchLedgerMode('live');

  } else {

    if (label) {

      label.textContent = 'DRY-RUN / PAPER TRADING (Safe Simulation)';

      label.style.color = 'var(--text-primary)';

    }

    if (pill) {

      pill.textContent = 'Mode: Dry-Run';

      pill.className = 'badge badge-cyan';

    }

    switchLedgerMode('paper');

  }

  checkStakeBalanceSafety();

}



async function startBot() {

  dismissBotError();

  const isExecute = DOM.chkExecuteLive ? DOM.chkExecuteLive.checked : false;

  if (isExecute && selectedStake > currentWalletUsdcBalance) {

    showBotError(`Cannot launch bot in live execution mode: Target stake ($${selectedStake.toFixed(2)}) exceeds wallet balance ($${currentWalletUsdcBalance.toFixed(2)}). Please deposit funds or switch to Paper Trading.`);

    return;

  }



  const payload = {

    profile: selectedProfile,

    execute: isExecute,

    stake_usd: selectedStake,

    threshold: selectedThreshold,

    run_mode: selectedRunMode,

  };



  const btnStart = DOM.btnStartBot || document.getElementById('btnStartBot');

  btnStart.disabled = true;

  btnStart.innerHTML = '<span style="display:inline-block; animation:spin 1s linear infinite;">⏳</span> Launching Bot...';



  try {

    const res = await fetch('/api/bot/start', {

      method: 'POST',

      headers: { 'Content-Type': 'application/json' },

      body: JSON.stringify(payload)

    });

    const result = await res.json();

    if (result.success) {

      fetchStatus();

      switchTab('logs');

    } else {

      showBotError(`Could not start bot: ${result.message || result.error}`);

      fetchStatus();

    }

  } catch (e) {

    showBotError(`Error starting bot: ${e}`);

    fetchStatus();

  }

}



async function stopBot() {

  const btnStop = DOM.btnStopBot || document.getElementById('btnStopBot');

  if (btnStop) {

    btnStop.disabled = true;

    btnStop.classList.remove('btn-emergency-stop');

    btnStop.innerHTML = '⏹ Stopping...';

  }



  try {

    const res = await fetch('/api/bot/stop', {

      method: 'POST',

      headers: { 'Content-Type': 'application/json' }

    });

    const result = await res.json();

    if (!result.success && result.error) {

      showBotError(`Could not stop bot: ${result.error}`);

    }

    fetchStatus();

    fetchTrades(currentLedgerMode);

  } catch (e) {

    showBotError(`Error stopping bot: ${e}`);

    fetchStatus();

  }

}



async function fetchStatus() {

  try {

    const res = await fetch('/api/status');

    const data = await res.json();

    const bot = data.bot || {};

    const badge = DOM.botStatusBadge || document.getElementById('botStatusBadge');

    const badgeText = DOM.botStatusText || document.getElementById('botStatusText');

    const btnStart = DOM.btnStartBot || document.getElementById('btnStartBot');

    const btnStop = DOM.btnStopBot || document.getElementById('btnStopBot');



    if (bot.running) {

      currentBotRunning = true;

      if (badge) badge.className = 'status-badge running';

      if (badgeText) badgeText.textContent = `BOT: RUNNING (PID ${bot.pid})`;

      if (btnStart) {

        btnStart.disabled = true;

        btnStart.classList.add('btn-disabled');

        btnStart.innerHTML = `⚡ ACTIVE (PID ${bot.pid})`;

      }

      if (btnStop) {

        btnStop.disabled = false;

        btnStop.classList.remove('btn-disabled');

        btnStop.innerHTML = '⏹ STOP SESSION <kbd class="kbd-hint">ESC</kbd>';



        // When running in LIVE mode, Stop button becomes prominent pulsing emergency kill switch

        const isLive = bot.mode === 'execute' || (DOM.chkExecuteLive && DOM.chkExecuteLive.checked);

        if (isLive) {

          btnStop.classList.add('btn-emergency-stop');

          btnStop.classList.remove('active-pulse');

        } else {

          btnStop.classList.remove('btn-emergency-stop');

          btnStop.classList.add('active-pulse');

        }

      }

      

      if (DOM.stateRunning) {

        DOM.stateRunning.textContent = 'ACTIVE';

        DOM.stateRunning.style.color = 'var(--emerald)';

      }

      if (DOM.statePid) DOM.statePid.textContent = bot.pid;

      if (DOM.stateMode) DOM.stateMode.textContent = bot.mode === 'execute' ? 'LIVE' : 'Dry-Run';

      if (DOM.stateRuntime) DOM.stateRuntime.textContent = bot.started_at || 'Just started';

    } else {

      currentBotRunning = false;

      if (badge) badge.className = 'status-badge ready';

      if (badgeText) badgeText.textContent = 'BOT: IDLE';

      if (btnStop) {

        btnStop.disabled = true;

        btnStop.classList.add('btn-disabled');

        btnStop.classList.remove('active-pulse');

        btnStop.classList.remove('btn-emergency-stop');

        btnStop.innerHTML = '⏹ STOP SESSION <kbd class="kbd-hint">ESC</kbd>';

      }



      if (DOM.stateRunning) {

        DOM.stateRunning.textContent = 'STOPPED';

        DOM.stateRunning.style.color = 'var(--text-muted)';

      }

      if (DOM.statePid) DOM.statePid.textContent = '—';

      if (DOM.stateRuntime) DOM.stateRuntime.textContent = '—';



      checkStakeBalanceSafety();

    }

  } catch (e) {

    // console.warn(e);

  }

}



/* ================= HISTORICAL TRADE LEDGER (PAPER VS REAL) ================= */

function switchLedgerMode(mode) {

  currentLedgerMode = mode;

  const isLive = mode === 'live';

  document.getElementById('btnLedgerPaper').classList.toggle('active', !isLive);

  document.getElementById('btnLedgerLive').classList.toggle('active', isLive);

  const badge = document.getElementById('ledgerModeBadge');

  const subtitle = document.getElementById('ledgerSubTitle');

  if (isLive) {

    badge.className = 'badge badge-danger';

    badge.textContent = 'Mode: Real Live Trades (Polygon On-Chain)';

    subtitle.textContent = 'Auditing verified on-chain executions, transaction hashes, and actual cashflows';

  } else {

    badge.className = 'badge badge-cyan';

    badge.textContent = 'Mode: Paper Trading (Simulation)';

    subtitle.textContent = 'Tracking simulated orders against live orderbook quotes and virtual PnL';

  }

  fetchTrades(mode);

}



function reloadTradeLedger() {

  fetchTrades(currentLedgerMode);

}



async function fetchTrades(mode = currentLedgerMode) {

  try {

    const res = await fetch(`/api/trades?mode=${mode}`);

    const data = await res.json();

    renderTradeLedger(data);

  } catch (e) {

    console.warn('Error fetching trade ledger:', e);

  }

}



function renderTradeLedger(data) {

  const stats = data.stats || {};

  const trades = data.trades || [];



  // Update summary ribbon

  document.getElementById('lmTotalTrades').textContent = stats.total_trades || 0;

  const wrEl = document.getElementById('lmWinRate');

  wrEl.textContent = `${stats.win_rate_pct || 0}%`;

  document.getElementById('lmWinsLosses').textContent = `${stats.wins || 0}W / ${stats.losses || 0}L`;



  const netPnl = stats.net_pnl_usdc || 0;

  const netEl = document.getElementById('lmNetPnl');

  netEl.textContent = `${netPnl >= 0 ? '+' : ''}$${netPnl.toFixed(2)} USDC`;

  netEl.className = `lm-val ${netPnl >= 0 ? 'text-emerald' : 'text-rose'}`;



  const avgPnl = stats.avg_pnl_usdc || 0;

  const avgEl = document.getElementById('lmAvgPnl');

  avgEl.textContent = `${avgPnl >= 0 ? '+' : ''}$${avgPnl.toFixed(2)}`;

  avgEl.className = `lm-val ${avgPnl >= 0 ? 'text-emerald' : 'text-rose'}`;



  document.getElementById('lmProfitFactor').textContent = (stats.profit_factor || 0).toFixed(2);



  // Render Table Rows

  const tbody = document.getElementById('tradeLedgerBody');

  if (!tbody) return;

  if (!trades || trades.length === 0) {

    tbody.innerHTML = `

      <tr>

        <td colspan="10" style="text-align: center; color: var(--text-muted); padding: 35px;">

          No historical trades recorded in <b>${data.mode === 'live' ? 'Real Live Trading' : 'Paper Trading'}</b> mode yet.

        </td>

      </tr>

    `;

    return;

  }



  tbody.innerHTML = '';

  trades.forEach(t => {

    const tr = document.createElement('tr');

    const pnl = Number(t.pnl_usdc || 0);

    const pnlPct = Number(t.pnl_pct || 0);

    const isWin = pnl > 0;

    const pnlClass = isWin ? 'text-emerald' : (pnl < 0 ? 'text-rose' : '');

    const timeStr = t.timestamp ? t.timestamp.replace('T', ' ').slice(11, 19) : '—';

    const dateStr = t.timestamp ? t.timestamp.slice(5, 10) : '';

    

    const sideBadge = t.side === 'UP' 

      ? '<span class="badge badge-cyan">🟢 UP</span>' 

      : '<span class="badge badge-purple">🟣 DOWN</span>';



    let reasonBadge = '<span class="badge badge-secondary">Exit</span>';

    if (t.exit_reason && t.exit_reason.includes('stop_loss')) {

      reasonBadge = '<span class="badge badge-rose">🛑 Stop-Loss</span>';

    } else if (t.exit_reason && t.exit_reason.includes('time_exit')) {

      reasonBadge = '<span class="badge badge-cyan">⏱️ Pre-Close</span>';

    }



    const statusBadge = isWin 

      ? '<span class="badge-win">✅ WIN</span>' 

      : '<span class="badge-loss">❌ LOSS</span>';



    const marketLink = t.market_slug 

      ? `<a href="https://polymarket.com/market/${t.market_slug}" target="_blank" class="link-polymarket" title="${t.market_slug}">${t.market_slug.length > 20 ? t.market_slug.slice(0, 18) + '...' : t.market_slug}</a>` 

      : '—';



    let txPreview = '—';

    if (t.tx_hash && !t.tx_hash.startsWith('sim_')) {

      txPreview = `<a href="https://polygonscan.com/tx/${t.tx_hash}" target="_blank" class="link-polymarket" title="${t.tx_hash}">0x${t.tx_hash.slice(2, 6)}...↗</a>`;

    } else if (t.order_id) {

      txPreview = `<span style="font-size: 10px; color: var(--text-muted);">${t.order_id.slice(0, 10)}...</span>`;

    }



    tr.innerHTML = `

      <td><strong>${timeStr}</strong> <span style="font-size: 10px; color: var(--text-muted);">${dateStr}</span></td>

      <td>${marketLink}</td>

      <td>${sideBadge}</td>

      <td><strong>$${Number(t.entry_price || 0).toFixed(3)}</strong></td>

      <td>$${Number(t.cost_usdc || 0).toFixed(2)} <span style="font-size: 10px; color: var(--text-muted);">(${Number(t.shares || 0).toFixed(1)} sh)</span></td>

      <td><strong>$${Number(t.exit_price || 0).toFixed(3)}</strong></td>

      <td>${reasonBadge}</td>

      <td class="${pnlClass}"><strong>${pnl >= 0 ? '+' : ''}$${pnl.toFixed(2)}</strong> <span style="font-size: 10px;">(${pnlPct >= 0 ? '+' : ''}${pnlPct.toFixed(1)}%)</span></td>

      <td>${statusBadge}</td>

      <td>${txPreview}</td>

    `;

    tbody.appendChild(tr);

  });

}



function formatTimestampForFilename(d = new Date()) {

  const pad = n => String(n).padStart(2, '0');

  const YYYY = d.getFullYear();

  const MM = pad(d.getMonth() + 1);

  const DD = pad(d.getDate());

  const hh = pad(d.getHours());

  const mm = pad(d.getMinutes());

  const ss = pad(d.getSeconds());

  return `${YYYY}-${MM}-${DD}_${hh}-${mm}-${ss}`;

}



function exportTradeHistoryCSV() {

  fetch(`/api/trades?mode=${currentLedgerMode}`)

    .then(r => r.json())

    .then(data => {

      const trades = data.trades || [];

      if (trades.length === 0) {

        showBotError(`No historical trades to export for ${currentLedgerMode === 'live' ? 'Real Live Trading' : 'Paper Trading'} mode.`);

        return;

      }

      const headers = ['id', 'timestamp', 'mode', 'market_slug', 'side', 'entry_price', 'shares', 'cost_usdc', 'exit_price', 'exit_usdc', 'exit_reason', 'pnl_usdc', 'pnl_pct', 'status', 'tx_hash'];

      let csv = headers.join(',') + '\n';

      trades.forEach(t => {

        const row = headers.map(h => `"${(t[h] !== undefined && t[h] !== null) ? String(t[h]).replace(/"/g, '""') : ''}"`);

        csv += row.join(',') + '\n';

      });

      const blob = new Blob([csv], { type: 'text/csv' });

      const url = URL.createObjectURL(blob);

      const a = document.createElement('a');

      a.href = url;

      a.download = `trades_${currentLedgerMode}_${formatTimestampForFilename()}.csv`;

      a.click();

      URL.revokeObjectURL(url);

    })

    .catch(err => {

      showBotError(`Export failed: ${err}`);

    });

}



/* ================= STRATEGY LAB COMPARATIVE LEADERBOARD ================= */

async function loadStrategyComparison() {

  const tbody = DOM.leaderboardBody || document.getElementById('leaderboardBody');

  if (!tbody) return;



  try {

    const res = await fetch('/api/strategy-comparison');

    const data = await res.json();

    if (data.error || !data.leaderboard) {

      tbody.innerHTML = `<tr><td colspan="8" style="text-align: center; color: var(--text-muted); padding: 18px;">${data.error || 'No comparison report available. Click "Re-Run All 5 Strategies" to generate.'}</td></tr>`;

      return;

    }



    const rows = data.leaderboard.map((item, idx) => {

      const rank = idx + 1;

      const rankClass = rank === 1 ? 'rank-1' : (rank === 2 ? 'rank-2' : (rank === 3 ? 'rank-3' : ''));

      const pnl = item.net_pnl_usd;

      const pnlClass = pnl > 0 ? 'text-emerald' : (pnl < 0 ? 'text-rose' : 'text-muted');

      const pnlSign = pnl > 0 ? '+' : '';

      const isDeployed = selectedProfile === item.strategy_id;

      const deployBtnClass = isDeployed ? 'btn-deploy-strat deployed' : 'btn-deploy-strat';

      const deployBtnText = isDeployed ? '✓ Active in Bot' : 'Deploy Strategy';



      return `

        <tr>

          <td>

            <div style="display: flex; align-items: center;">

              <span class="leaderboard-rank ${rankClass}">${rank}</span>

              <div>

                <div class="strat-cell-title">${item.name}</div>

                <div class="strat-cell-desc">${item.description}</div>

              </div>

            </div>

          </td>

          <td><b>${item.total_trades}</b></td>

          <td><b class="${item.win_rate_pct >= 85 ? 'text-emerald' : ''}">${item.win_rate_pct.toFixed(1)}%</b></td>

          <td><b class="text-cyan">${item.payoff_ratio >= 99 ? '99.0x' : item.payoff_ratio.toFixed(2) + 'x'}</b></td>

          <td><b class="text-purple">${item.profit_factor >= 99 ? '99.0x' : item.profit_factor.toFixed(2) + 'x'}</b></td>

          <td><b class="${pnlClass}">${pnlSign}$${pnl.toFixed(2)}</b></td>

          <td>$${item.max_drawdown_usd.toFixed(2)}</td>

          <td>

            <button class="${deployBtnClass}" data-strat-id="${item.strategy_id}" onclick="deployStrategy('${item.strategy_id}')">

              ${deployBtnText}

            </button>

          </td>

        </tr>

      `;

    }).join('');



    tbody.innerHTML = rows;

  } catch (e) {

    console.error('Failed to load strategy comparison:', e);

  }

}



async function runMultiBacktest() {

  const btn = DOM.btnRunMultiBacktest || document.getElementById('btnRunMultiBacktest');

  if (btn) {

    btn.disabled = true;

    btn.innerHTML = '<span style="display:inline-block; animation:spin 1s linear infinite;">⏳</span> Running 5 Strategies (288 intervals)...';

  }



  try {

    const res = await fetch('/api/run-multi-backtest', { method: 'POST' });

    const data = await res.json();

    if (data.success) {

      setTimeout(() => {

        loadStrategyComparison();

        if (btn) {

          btn.disabled = false;

          btn.innerHTML = '⚡ Re-Run All 5 Strategies';

        }

      }, 4500);

    }

  } catch (e) {

    if (btn) {

      btn.disabled = false;

      btn.innerHTML = '⚡ Re-Run All 5 Strategies';

    }

  }

}



/* ================= BACKTEST REPORT (SUNDAY 24H REPLAY) ================= */

async function loadBacktestData() {

  try {

    const res = await fetch('/api/backtest');

    const data = await res.json();

    if (data.error) return;

    backtestData = data;

    renderBacktestMetrics(data);

    renderEquityChart(data, activeChartFilter);

    renderTable(data.intervals || []);

  } catch (e) {

    console.error('Failed to load backtest data:', e);

  }

}



function reloadBacktest() {

  loadBacktestData();

}



function renderBacktestMetrics(data) {

  const cap = data.strict_profile_results || {};

  const unc = data.uncapped_results || {};



  document.getElementById('valWinRate').textContent = `${unc.win_rate_pct}%`;

  document.getElementById('valWinsLosses').textContent = `${unc.wins} Wins / ${unc.stop_losses_hit || 0} Stop-Losses (Uncapped)`;

  document.getElementById('valNetPnl').textContent = `$${unc.net_pnl_usd > 0 ? '+' : ''}${unc.net_pnl_usd.toFixed(2)}`;

  document.getElementById('valUncappedPnl').textContent = `Strict 12-Trade Cap: $${cap.net_pnl_usd > 0 ? '+' : ''}${cap.net_pnl_usd.toFixed(2)} USDC`;

  document.getElementById('valSignalsRatio').textContent = `${unc.total_signals} / ${data.total_5m_intervals}`;

  document.getElementById('valMaxDd').textContent = `$${unc.max_drawdown_usd.toFixed(2)}`;

}



function setChartFilter(mode) {

  activeChartFilter = mode;

  document.getElementById('btnChartAll').classList.toggle('active', mode === 'all');

  document.getElementById('btnChartCapped').classList.toggle('active', mode === 'capped');

  if (backtestData) {

    renderEquityChart(backtestData, mode);

  }

}



function renderEquityChart(data, filterMode) {

  const canvas = DOM.equityChart || document.getElementById('equityChart');

  if (!canvas) return;

  const ctx = canvas.getContext('2d');

  const intervals = data.intervals || [];



  let labels = [];

  let points = [];

  let cum = 0;

  let tradeCount = 0;



  intervals.forEach(item => {

    if (item.traded) {

      tradeCount++;

      if (filterMode === 'capped' && tradeCount > 12) {

        return;

      }

      cum += item.net_pnl_usd;

      labels.push(item.time_utc);

      points.push(Number(cum.toFixed(2)));

    }

  });



  const datasetLabel = filterMode === 'capped' 

    ? 'Strict Cap: First 12 Trades Cumulative PnL (USDC)' 

    : 'All 52 Signals Cumulative PnL (USDC)';



  if (chartInstance) {

    chartInstance.data.labels = labels;

    chartInstance.data.datasets[0].data = points;

    chartInstance.data.datasets[0].label = datasetLabel;

    chartInstance.update('none');

    return;

  }



  const gradient = ctx.createLinearGradient(0, 0, 0, 350);

  gradient.addColorStop(0, 'rgba(6, 182, 212, 0.4)');

  gradient.addColorStop(1, 'rgba(6, 182, 212, 0.0)');



  chartInstance = new Chart(ctx, {

    type: 'line',

    data: {

      labels: labels,

      datasets: [{

        label: datasetLabel,

        data: points,

        borderColor: '#06b6d4',

        backgroundColor: gradient,

        borderWidth: 2.5,

        fill: true,

        tension: 0.25,

        pointRadius: 3.5,

        pointBackgroundColor: '#06b6d4',

        pointHoverRadius: 6,

      }]

    },

    options: {

      responsive: true,

      maintainAspectRatio: false,

      plugins: {

        legend: {

          labels: { color: '#94a3b8', font: { family: 'Outfit', size: 12, weight: 600 } }

        },

        tooltip: {

          backgroundColor: 'rgba(15, 20, 32, 0.95)',

          titleColor: '#f8fafc',

          bodyColor: '#06b6d4',

          borderColor: 'rgba(6, 182, 212, 0.3)',

          borderWidth: 1,

          padding: 12,

          callbacks: {

            label: function(context) {

              return ` Cumulative PnL: $${context.parsed.y > 0 ? '+' : ''}${context.parsed.y.toFixed(2)} USDC`;

            }

          }

        }

      },

      scales: {

        x: {

          grid: { color: 'rgba(255, 255, 255, 0.04)' },

          ticks: { color: '#64748b', font: { family: 'JetBrains Mono', size: 11 } }

        },

        y: {

          grid: { color: 'rgba(255, 255, 255, 0.04)' },

          ticks: {

            color: '#64748b',

            font: { family: 'JetBrains Mono', size: 11 },

            callback: value => `$${value}`

          }

        }

      }

    }

  });

}



function renderTable(intervals) {

  const tbody = document.getElementById('tableBody');

  tbody.innerHTML = '';



  const filterStatus = document.getElementById('filterTradeStatus').value;

  const search = document.getElementById('tableSearch').value.trim().toLowerCase();



  let filtered = intervals.filter(item => {

    if (filterStatus === 'traded' && !item.traded) return false;

    if (filterStatus === 'wins' && item.result !== 'WIN') return false;

    if (filterStatus === 'losses' && item.result !== 'SL_HIT' && item.result !== 'LOSS') return false;

    if (filterStatus === 'skipped' && item.traded) return false;

    if (search && !item.time_utc.toLowerCase().includes(search) && !item.slug.toLowerCase().includes(search)) return false;

    return true;

  });



  filtered.forEach(row => {

    const tr = document.createElement('tr');



    let badgeClass = 'badge-secondary';

    let resultText = 'SKIPPED';

    if (row.result === 'WIN') {

      badgeClass = 'badge-success';

      resultText = '✅ WIN';

    } else if (row.result === 'SL_HIT') {

      badgeClass = 'badge-rose';

      resultText = '🛑 SL HIT';

    } else if (row.result === 'LOSS') {

      badgeClass = 'badge-rose';

      resultText = '❌ LOSS';

    }



    const pnlColor = row.net_pnl_usd > 0 ? 'text-emerald' : (row.net_pnl_usd < 0 ? 'text-rose' : '');

    const pnlText = row.traded ? `$${row.net_pnl_usd > 0 ? '+' : ''}${row.net_pnl_usd.toFixed(2)}` : '—';

    const cumPnlText = row.cumulative_pnl_usd !== undefined && row.traded ? `$${row.cumulative_pnl_usd > 0 ? '+' : ''}${row.cumulative_pnl_usd.toFixed(2)}` : '—';



    tr.innerHTML = `

      <td><strong>${row.time_utc}</strong></td>

      <td style="color: var(--text-muted); font-size: 11px;">${row.slug}</td>

      <td>$${row.btc_open ? row.btc_open.toFixed(1) : '—'}</td>

      <td>$${row.btc_entry ? row.btc_entry.toFixed(1) : '—'}</td>

      <td style="color: ${row.btc_impulse > 0 ? 'var(--emerald)' : (row.btc_impulse < 0 ? 'var(--rose)' : 'inherit')};">

        ${row.btc_impulse !== null ? `${row.btc_impulse > 0 ? '+' : ''}$${row.btc_impulse.toFixed(1)}` : '—'}

      </td>

      <td><span class="badge ${row.signal_side === 'UP' ? 'badge-cyan' : (row.signal_side === 'DOWN' ? 'badge-purple' : '')}">${row.signal_side || '—'}</span></td>

      <td>${row.entry_price ? `$${row.entry_price.toFixed(3)}` : '—'}</td>

      <td>${row.exit_price ? `$${row.exit_price.toFixed(2)}` : '—'}</td>

      <td><span style="font-size: 10px; color: var(--text-muted);">${row.exit_type || '—'}</span></td>

      <td><span class="badge ${badgeClass}">${resultText}</span></td>

      <td class="${pnlColor}"><strong>${pnlText}</strong></td>

      <td><strong>${cumPnlText}</strong></td>

    `;

    tbody.appendChild(tr);

  });

}



let tableSearchDebounceTimer = null;

function filterTableDebounced() {

  clearTimeout(tableSearchDebounceTimer);

  tableSearchDebounceTimer = setTimeout(() => {

    filterTable();

  }, 200);

}



function filterTable() {

  if (backtestData && backtestData.intervals) {

    renderTable(backtestData.intervals);

  }

}



/* ================= TERMINAL LOGS & QUICK FILTERS ================= */

async function fetchLogs() {

  try {

    const res = await fetch('/api/logs');

    const data = await res.json();

    rawLogsCache = data.logs || '';



    if (data.file) {

      document.getElementById('logFileHeader').textContent = `Reading: runtime/${data.file}`;

    }



    renderFilteredLogs();

  } catch (e) {

    // console.warn(e);

  }

}



function setLogFilter(filter) {

  activeLogFilter = filter;

  document.querySelectorAll('.log-filter-btn').forEach(b => {

    b.classList.toggle('active', b.dataset.filter === filter);

  });

  renderFilteredLogs();

}



function renderFilteredLogs() {

  const terminal = document.getElementById('terminalOutput');

  if (!terminal) return;

  if (!rawLogsCache) {

    terminal.textContent = 'No bot runtime logs available yet. Start bot above to stream output.';

    return;

  }



  let lines = rawLogsCache.split('\n');

  if (activeLogFilter === 'signal') {

    lines = lines.filter(l => l.includes('IMPULSE') || l.includes('SIGNAL') || l.includes('HOLDING') || l.includes('TRIGGER') || l.includes('FORMATION'));

  } else if (activeLogFilter === 'order') {

    lines = lines.filter(l => l.includes('POSITION') || l.includes('ORDER') || l.includes('PnL') || l.includes('CLOSED') || l.includes('matched') || l.includes('HISTORY'));

  } else if (activeLogFilter === 'error') {

    lines = lines.filter(l => l.includes('ERROR') || l.includes('WARN') || l.includes('failed') || l.includes('Timeout') || l.includes('not matched'));

  }



  terminal.textContent = lines.join('\n') || `(No log events matching filter "${activeLogFilter}")`;



  if (document.getElementById('chkAutoScroll') && document.getElementById('chkAutoScroll').checked) {

    terminal.scrollTop = terminal.scrollHeight;

  }

}



/* ================= BTC SPOT MICRO-SPARKLINE ================= */

function updateSparkline(price) {

  if (!price || isNaN(price)) return;

  sparklineTicks.push(Number(price));

  if (sparklineTicks.length > 35) {

    sparklineTicks.shift();

  }

  drawSparkline();

}



function drawSparkline() {

  const canvas = document.getElementById('btcSparkline');

  if (!canvas) return;

  const ctx = canvas.getContext('2d');

  const w = canvas.width = canvas.parentElement.clientWidth || 300;

  const h = canvas.height = 48;

  if (sparklineTicks.length < 2) return;



  ctx.clearRect(0, 0, w, h);



  const min = Math.min(...sparklineTicks);

  const max = Math.max(...sparklineTicks);

  const range = (max - min) || 1.0;

  const pad = 4;



  const points = sparklineTicks.map((val, idx) => {

    const x = (idx / (sparklineTicks.length - 1)) * (w - 2 * pad) + pad;

    const y = h - pad - ((val - min) / range) * (h - 2 * pad);

    return { x, y };

  });



  const isUp = sparklineTicks[sparklineTicks.length - 1] >= sparklineTicks[0];

  const strokeColor = isUp ? '#10b981' : '#f43f5e';



  // Fill Gradient

  const grad = ctx.createLinearGradient(0, 0, 0, h);

  grad.addColorStop(0, isUp ? 'rgba(16, 185, 129, 0.35)' : 'rgba(244, 63, 94, 0.35)');

  grad.addColorStop(1, 'rgba(15, 20, 32, 0.0)');



  ctx.beginPath();

  ctx.moveTo(points[0].x, points[0].y);

  for (let i = 1; i < points.length; i++) {

    const xc = (points[i].x + points[i - 1].x) / 2;

    const yc = (points[i].y + points[i - 1].y) / 2;

    ctx.quadraticCurveTo(points[i - 1].x, points[i - 1].y, xc, yc);

  }

  ctx.lineTo(points[points.length - 1].x, points[points.length - 1].y);

  ctx.strokeStyle = strokeColor;

  ctx.lineWidth = 2;

  ctx.stroke();



  // Fill area under curve

  ctx.lineTo(points[points.length - 1].x, h);

  ctx.lineTo(points[0].x, h);

  ctx.closePath();

  ctx.fillStyle = grad;

  ctx.fill();



  // Trend indicator text

  const trendEl = document.getElementById('sparklineTrend');

  if (trendEl) {

    const delta = sparklineTicks[sparklineTicks.length - 1] - sparklineTicks[0];

    trendEl.textContent = `${delta >= 0 ? '▲ +' : '▼ -'}$${Math.abs(delta).toFixed(1)}`;

    trendEl.className = `sparkline-trend ${delta >= 0 ? 'text-emerald' : 'text-rose'}`;

  }

}



/* ================= INTERACTIVE STRATEGY CONFIGURATION ================= */

async function loadActiveSettings() {

  try {

    const res = await fetch('/api/settings');

    const data = await res.json();

    if (!data.success || !data.config) return;

    const profiles = data.config.profiles || {};



    // 1. Momentum Value

    if (profiles.momentum_value) {

      const p = profiles.momentum_value;

      if (p.signal?.threshold_price && DOM.cfg_mv_threshold) DOM.cfg_mv_threshold.value = p.signal.threshold_price;

      if (p.signal?.max_entry_price && DOM.cfg_mv_max_entry) DOM.cfg_mv_max_entry.value = p.signal.max_entry_price;

      if (p.signal?.min_btc_impulse && DOM.cfg_mv_impulse) DOM.cfg_mv_impulse.value = p.signal.min_btc_impulse;

      if (p.sizing?.stake_usd && DOM.cfg_mv_stake) DOM.cfg_mv_stake.value = p.sizing.stake_usd;

      if (p.take_profit?.take_profit_pct && DOM.cfg_mv_tp) DOM.cfg_mv_tp.value = Math.round(p.take_profit.take_profit_pct * 100);

      if (p.stop_loss?.stop_loss_pct_from_entry && DOM.cfg_mv_sl) DOM.cfg_mv_sl.value = Math.round(p.stop_loss.stop_loss_pct_from_entry * 100);

      if (p.sizing?.max_trades_per_day && DOM.cfg_mv_cap) DOM.cfg_mv_cap.value = p.sizing.max_trades_per_day;

    }



    // 2. Quick Scalp

    if (profiles.quick_scalp) {

      const p = profiles.quick_scalp;

      if (p.signal?.threshold_price && DOM.cfg_qs_threshold) DOM.cfg_qs_threshold.value = p.signal.threshold_price;

      if (p.signal?.max_entry_price && DOM.cfg_qs_max_entry) DOM.cfg_qs_max_entry.value = p.signal.max_entry_price;

      if (p.signal?.min_btc_impulse && DOM.cfg_qs_impulse) DOM.cfg_qs_impulse.value = p.signal.min_btc_impulse;

      if (p.sizing?.stake_usd && DOM.cfg_qs_stake) DOM.cfg_qs_stake.value = p.sizing.stake_usd;

      if (p.take_profit?.take_profit_pct && DOM.cfg_qs_tp) DOM.cfg_qs_tp.value = Math.round(p.take_profit.take_profit_pct * 100);

      if (p.trailing_stop?.trailing_stop_pct && DOM.cfg_qs_trailing) DOM.cfg_qs_trailing.value = Math.round(p.trailing_stop.trailing_stop_pct * 100);

      if (p.stop_loss?.stop_loss_pct_from_entry && DOM.cfg_qs_sl) DOM.cfg_qs_sl.value = Math.round(p.stop_loss.stop_loss_pct_from_entry * 100);

      if (p.sizing?.max_trades_per_day && DOM.cfg_qs_cap) DOM.cfg_qs_cap.value = p.sizing.max_trades_per_day;

    }



    // 3. Mean Reversion

    if (profiles.mean_reversion) {

      const p = profiles.mean_reversion;

      if (p.signal?.favorite_trigger_price && DOM.cfg_mr_fav_trigger) DOM.cfg_mr_fav_trigger.value = p.signal.favorite_trigger_price;

      if (p.signal?.max_entry_price && DOM.cfg_mr_max_entry) DOM.cfg_mr_max_entry.value = p.signal.max_entry_price;

      if (p.sizing?.stake_usd && DOM.cfg_mr_stake) DOM.cfg_mr_stake.value = p.sizing.stake_usd;

      if (p.take_profit?.take_profit_pct && DOM.cfg_mr_tp) DOM.cfg_mr_tp.value = Math.round(p.take_profit.take_profit_pct * 100);

      if (p.stop_loss?.stop_loss_pct_from_entry && DOM.cfg_mr_sl) DOM.cfg_mr_sl.value = Math.round(p.stop_loss.stop_loss_pct_from_entry * 100);

      if (p.sizing?.max_trades_per_day && DOM.cfg_mr_cap) DOM.cfg_mr_cap.value = p.sizing.max_trades_per_day;

    }



    // 4. Skew Tail Hedge

    if (profiles.skew_hedge) {

      const p = profiles.skew_hedge;

      if (p.signal?.threshold_price && DOM.cfg_sh_threshold) DOM.cfg_sh_threshold.value = p.signal.threshold_price;

      if (p.signal?.max_entry_price && DOM.cfg_sh_max_entry) DOM.cfg_sh_max_entry.value = p.signal.max_entry_price;

      if (p.sizing?.stake_usd && DOM.cfg_sh_stake) DOM.cfg_sh_stake.value = p.sizing.stake_usd;

      if (p.stop_loss?.stop_loss_pct_from_entry && DOM.cfg_sh_sl) DOM.cfg_sh_sl.value = Math.round(p.stop_loss.stop_loss_pct_from_entry * 100);

      if (p.sizing?.max_trades_per_day && DOM.cfg_sh_cap) DOM.cfg_sh_cap.value = p.sizing.max_trades_per_day;

    }



    // 5. Macro Trend Sniper

    if (profiles.macro_trend_sniper) {

      const p = profiles.macro_trend_sniper;

      if (p.signal?.threshold_price && DOM.cfg_ts_threshold) DOM.cfg_ts_threshold.value = p.signal.threshold_price;

      if (p.signal?.max_entry_price && DOM.cfg_ts_max_entry) DOM.cfg_ts_max_entry.value = p.signal.max_entry_price;

      if (p.signal?.min_btc_impulse && DOM.cfg_ts_impulse) DOM.cfg_ts_impulse.value = p.signal.min_btc_impulse;

      if (p.sizing?.stake_usd && DOM.cfg_ts_stake) DOM.cfg_ts_stake.value = p.sizing.stake_usd;

      if (p.take_profit?.take_profit_pct && DOM.cfg_ts_tp) DOM.cfg_ts_tp.value = Math.round(p.take_profit.take_profit_pct * 100);

      if (p.stop_loss?.stop_loss_pct_from_entry && DOM.cfg_ts_sl) DOM.cfg_ts_sl.value = Math.round(p.stop_loss.stop_loss_pct_from_entry * 100);

      if (p.sizing?.max_trades_per_day && DOM.cfg_ts_cap) DOM.cfg_ts_cap.value = p.sizing.max_trades_per_day;

    }



    checkStakeBalanceSafety();

  } catch (e) {

    console.warn('Error loading settings:', e);

  }

}



async function saveActiveSettings() {

  const payload = {

    momentum_value: {

      threshold_price: DOM.cfg_mv_threshold ? parseFloat(DOM.cfg_mv_threshold.value) : 0.68,

      max_entry_price: DOM.cfg_mv_max_entry ? parseFloat(DOM.cfg_mv_max_entry.value) : 0.78,

      min_btc_impulse: DOM.cfg_mv_impulse ? parseFloat(DOM.cfg_mv_impulse.value) : 65.0,

      stake_usd: DOM.cfg_mv_stake ? parseFloat(DOM.cfg_mv_stake.value) : 5.0,

      take_profit_pct: DOM.cfg_mv_tp ? parseFloat(DOM.cfg_mv_tp.value) / 100.0 : 0.22,

      stop_loss_pct_from_entry: DOM.cfg_mv_sl ? parseFloat(DOM.cfg_mv_sl.value) / 100.0 : 0.25,

      max_trades_per_day: DOM.cfg_mv_cap ? parseInt(DOM.cfg_mv_cap.value) : 16,

    },

    quick_scalp: {

      threshold_price: DOM.cfg_qs_threshold ? parseFloat(DOM.cfg_qs_threshold.value) : 0.70,

      max_entry_price: DOM.cfg_qs_max_entry ? parseFloat(DOM.cfg_qs_max_entry.value) : 0.80,

      min_btc_impulse: DOM.cfg_qs_impulse ? parseFloat(DOM.cfg_qs_impulse.value) : 55.0,

      stake_usd: DOM.cfg_qs_stake ? parseFloat(DOM.cfg_qs_stake.value) : 5.0,

      take_profit_pct: DOM.cfg_qs_tp ? parseFloat(DOM.cfg_qs_tp.value) / 100.0 : 0.12,

      trailing_stop_pct: DOM.cfg_qs_trailing ? parseFloat(DOM.cfg_qs_trailing.value) / 100.0 : 0.08,

      stop_loss_pct_from_entry: DOM.cfg_qs_sl ? parseFloat(DOM.cfg_qs_sl.value) / 100.0 : 0.18,

      max_trades_per_day: DOM.cfg_qs_cap ? parseInt(DOM.cfg_qs_cap.value) : 24,

    },

    mean_reversion: {

      favorite_trigger_price: DOM.cfg_mr_fav_trigger ? parseFloat(DOM.cfg_mr_fav_trigger.value) : 0.85,

      max_entry_price: DOM.cfg_mr_max_entry ? parseFloat(DOM.cfg_mr_max_entry.value) : 0.25,

      stake_usd: DOM.cfg_mr_stake ? parseFloat(DOM.cfg_mr_stake.value) : 5.0,

      take_profit_pct: DOM.cfg_mr_tp ? parseFloat(DOM.cfg_mr_tp.value) / 100.0 : 1.50,

      stop_loss_pct_from_entry: DOM.cfg_mr_sl ? parseFloat(DOM.cfg_mr_sl.value) / 100.0 : 0.50,

      max_trades_per_day: DOM.cfg_mr_cap ? parseInt(DOM.cfg_mr_cap.value) : 12,

    },

    skew_hedge: {

      threshold_price: DOM.cfg_sh_threshold ? parseFloat(DOM.cfg_sh_threshold.value) : 0.70,

      max_entry_price: DOM.cfg_sh_max_entry ? parseFloat(DOM.cfg_sh_max_entry.value) : 0.82,

      stake_usd: DOM.cfg_sh_stake ? parseFloat(DOM.cfg_sh_stake.value) : 5.0,

      stop_loss_pct_from_entry: DOM.cfg_sh_sl ? parseFloat(DOM.cfg_sh_sl.value) / 100.0 : 0.25,

      max_trades_per_day: DOM.cfg_sh_cap ? parseInt(DOM.cfg_sh_cap.value) : 12,

    },

    macro_trend_sniper: {

      threshold_price: DOM.cfg_ts_threshold ? parseFloat(DOM.cfg_ts_threshold.value) : 0.72,

      max_entry_price: DOM.cfg_ts_max_entry ? parseFloat(DOM.cfg_ts_max_entry.value) : 0.82,

      min_btc_impulse: DOM.cfg_ts_impulse ? parseFloat(DOM.cfg_ts_impulse.value) : 85.0,

      stake_usd: DOM.cfg_ts_stake ? parseFloat(DOM.cfg_ts_stake.value) : 5.0,

      take_profit_pct: DOM.cfg_ts_tp ? parseFloat(DOM.cfg_ts_tp.value) / 100.0 : 0.28,

      stop_loss_pct_from_entry: DOM.cfg_ts_sl ? parseFloat(DOM.cfg_ts_sl.value) / 100.0 : 0.20,

      max_trades_per_day: DOM.cfg_ts_cap ? parseInt(DOM.cfg_ts_cap.value) : 10,

    }

  };



  try {

    const res = await fetch('/api/settings', {

      method: 'POST',

      headers: { 'Content-Type': 'application/json' },

      body: JSON.stringify(payload)

    });

    const data = await res.json();

    const statusBox = DOM.settingsStatusMsg || document.getElementById('settingsStatusMsg');

    if (statusBox) {

      statusBox.style.display = 'block';

      if (data.success) {

        statusBox.textContent = '✅ Strategy configurations saved and applied successfully across all 5 archetypes!';

        statusBox.style.background = 'rgba(16, 185, 129, 0.15)';

        statusBox.style.color = 'var(--emerald)';

        checkStakeBalanceSafety();

      } else {

        statusBox.textContent = `❌ Error saving settings: ${data.error}`;

        statusBox.style.background = 'rgba(244, 63, 94, 0.15)';

        statusBox.style.color = 'var(--rose)';

      }

      setTimeout(() => { statusBox.style.display = 'none'; }, 4000);

    }

  } catch (e) {

    showBotError(`Could not save settings: ${e}`);

  }

}



/* ==================== JEV AI AUTONOMOUS BRAIN CONTROLLER ==================== */

let jevMode = 'paper';

let jevTelemetryInterval = null;

let jevDecisionHistory = [];



function setJevMode(mode) {

  jevMode = mode;

  const btnSim = document.getElementById('btnJevModeSim');

  const btnLive = document.getElementById('btnJevModeLive');

  if (btnSim) btnSim.classList.toggle('active', mode === 'paper');

  if (btnLive) btnLive.classList.toggle('active', mode === 'live');

}



function updateJevStakeConfig() {

  const baseEl = document.getElementById('jevBaseStakeInput');

  const maxEl = document.getElementById('jevMaxStakeInput');

  if (baseEl && maxEl) {

    let base = parseFloat(baseEl.value) || 5;

    let max = parseFloat(maxEl.value) || 10;

    if (max < base) {

      max = base * 2;

      maxEl.value = max;

    }

  }

}





// ==========================================================================

// PRO TRADING TERMINAL — JEV AI AUTONOMOUS BRAIN CONTROLLER

// ==========================================================================



let jevPollInterval = null;

let jevLastDecisionTimestamp = null;

let jevImpulseHistory = [];

let jevLastCandleSlug = '';



// Quick Stake Selector

window.setQuickStake = function(amount) {

  const baseInput = document.getElementById('jevBaseStakeInput');

  const maxInput = document.getElementById('jevMaxStakeInput');

  if (baseInput) baseInput.value = (amount * 0.5).toFixed(1);

  if (maxInput) maxInput.value = amount.toFixed(1);



  document.querySelectorAll('.stake-pill').forEach(btn => {

    btn.classList.toggle('active', btn.textContent.includes(amount.toString()));

  });

};



// Mode Toggle

document.addEventListener('DOMContentLoaded', () => {

  const btnPaper = document.getElementById('jevModePaper');

  const btnLive = document.getElementById('jevModeLive');

  if (btnPaper && btnLive) {

    btnPaper.addEventListener('click', () => {

      btnPaper.classList.add('active');

      btnLive.classList.remove('active');

    });

    btnLive.addEventListener('click', () => {

      if (confirm('CAUTION: Switch Jev Autonomous Brain to LIVE REAL POLYMARKET CLOB trading?')) {

        btnLive.classList.add('active');

        btnPaper.classList.remove('active');

      }

    });

  }



  // Button Listeners

  const btnStart = document.getElementById('btnStartJevBot');

  const btnStop = document.getElementById('btnStopJevBot');

  const btnHalt = document.getElementById('btnEmergencyHaltJev');

  const btnTest = document.getElementById('btnTestJevDecision');



  if (btnStart) btnStart.addEventListener('click', startJevAutonomousBot);

  if (btnStop) btnStop.addEventListener('click', stopJevAutonomousBot);

  if (btnHalt) btnHalt.addEventListener('click', emergencyHaltJevBot);

  if (btnTest) btnTest.addEventListener('click', testJevBrainSingleTick);



  // Tab switching initialization

  const jevTabBtn = document.getElementById('nav-jev-ai');

  if (jevTabBtn) {

    jevTabBtn.addEventListener('click', () => {

      startJevPolling();

      setTimeout(renderCanvasGrid, 100);

    });

  }



  // Start polling if already on Jev tab

  const activePane = document.querySelector('.tab-pane.active');

  if (activePane && activePane.id === 'tab-jev-ai') {

    startJevPolling();

  }

});



function startJevPolling() {

  if (jevPollInterval) clearInterval(jevPollInterval);

  fetchJevTelemetry();

  jevPollInterval = setInterval(fetchJevTelemetry, 1000);

}



async function fetchJevTelemetry() {

  try {

    const res = await fetch('/api/jev/telemetry');

    if (!res.ok) return;

    const data = await res.json();

    renderProJevTelemetry(data);

  } catch (e) {

    // console.warn('Jev telemetry polling error:', e);

  }

}



function renderProJevTelemetry(data) {

  const mkt = data.market || {};

  const vel = data.velocity || {};

  const dec = data.decision || null;

  const botInfo = data.bot_info || {};

  const isRunning = data.bot_running || false;



  // 1. TOP MARKET STATS BAR

  const elSlug = document.getElementById('jevMarketSlug');

  if (elSlug && mkt.slug) elSlug.textContent = mkt.slug;



  const elSpot = document.getElementById('jevBtcSpot');

  if (elSpot) {

    elSpot.textContent = mkt.btc_spot ? `$${mkt.btc_spot.toLocaleString(undefined, {minimumFractionDigits: 1, maximumFractionDigits: 1})}` : '$---,---.-';

  }



  const elImp = document.getElementById('jevNetImpulse');

  if (elImp && mkt.btc_impulse !== null && mkt.btc_impulse !== undefined) {

    const sign = mkt.btc_impulse >= 0 ? '+' : '';

    elImp.textContent = `${sign}$${mkt.btc_impulse.toFixed(1)}`;

    elImp.style.color = mkt.btc_impulse >= 0 ? 'var(--emerald)' : 'var(--rose)';

  }



  const elOpen = document.getElementById('jevBtcOpen');

  if (elOpen) {

    elOpen.textContent = mkt.btc_open ? `Open: $${mkt.btc_open.toLocaleString(undefined, {minimumFractionDigits: 1, maximumFractionDigits: 1})}` : 'Open: $---,---';

  }



  // Micro Metrics

  const elVel = document.getElementById('jevImpulseVelocity');

  if (elVel) {

    const v15 = vel.velocity_15s || 0;

    const sign = v15 >= 0 ? '+' : '';

    elVel.textContent = `${sign}${v15.toFixed(1)} $/s`;

    elVel.style.color = v15 >= 0 ? 'var(--emerald)' : 'var(--rose)';

  }



  const elAcc = document.getElementById('jevAcceleration');

  if (elAcc) {

    const acc = vel.acceleration || 0;

    const sign = acc >= 0 ? '+' : '';

    elAcc.textContent = `${sign}${acc.toFixed(2)} $/s²`;

    elAcc.style.color = acc >= 0 ? 'var(--cyan)' : 'var(--amber)';

  }



  const elClobAsks = document.getElementById('jevClobAsks');

  if (elClobAsks) {

    const uAsk = mkt.up_ask ? `$${mkt.up_ask.toFixed(2)}` : '$--';

    const dAsk = mkt.dn_ask ? `$${mkt.dn_ask.toFixed(2)}` : '$--';

    elClobAsks.textContent = `UP ${uAsk} / DN ${dAsk}`;

  }



  const elClobSpread = document.getElementById('jevClobSpread');

  if (elClobSpread) {

    const skUp = Math.round(mkt.skew_up || 50);

    elClobSpread.textContent = `${skUp}% UP / ${100 - skUp}% DN`;

  }



  // Institutional Micro Metrics

  const inst = data.institutional || {};

  const elJevOi = document.getElementById('jevOpenInterest');

  if (elJevOi && inst.oi_usd_formatted) {

    const deltaStr = inst.oi_5m_delta_formatted || '+$0.0M';

    const isDeltaPositive = !deltaStr.startsWith('-');

    const deltaColor = isDeltaPositive ? 'var(--emerald)' : 'var(--rose)';

    elJevOi.innerHTML = `${inst.oi_usd_formatted} <span style="font-size:10px; color:${deltaColor};" id="jevOiDelta">(${deltaStr})</span>`;

  }



  const elJevCbPrem = document.getElementById('jevCoinbasePremium');

  if (elJevCbPrem && inst.coinbase_premium !== undefined) {

    const prem = inst.coinbase_premium;

    const sign = prem >= 0 ? '+' : '';

    elJevCbPrem.textContent = `${sign}$${prem.toFixed(2)}`;

    elJevCbPrem.style.color = prem >= 0 ? 'var(--emerald)' : 'var(--rose)';

  }



  // Expiry Timer

  const elSec = document.getElementById('jevSecondsLeft');

  if (elSec) {

    const s = mkt.seconds_left || 0;

    const mins = Math.floor(s / 60);

    const secs = s % 60;

    elSec.textContent = `${String(mins).padStart(2, '0')}:${String(secs).padStart(2, '0')}`;

    if (s < 20) elSec.style.color = 'var(--rose)';

    else if (s < 60) elSec.style.color = 'var(--amber)';

    else elSec.style.color = 'var(--cyan)';

  }



  // Engine Chips

  const elStatusChip = document.getElementById('jevEngineStatusChip');

  if (elStatusChip) {

    elStatusChip.textContent = data.jev_ready ? 'Online & Authenticated' : 'Offline / Heuristic';

    elStatusChip.style.color = data.jev_ready ? 'var(--emerald)' : 'var(--amber)';

  }



  const elLatChip = document.getElementById('jevLatencyChip');

  if (elLatChip) {

    const lat = dec && dec.latency_ms ? Math.round(dec.latency_ms) : 95;

    elLatChip.textContent = `<1ms Feed / ${lat}ms Jev`;

  }



  // 2. REAL-TIME IMPULSE CANVAS & VELOCITY THRUST GAUGE

  updateImpulseChart(mkt.slug, mkt.btc_impulse || 0);

  updateVelocityThrust(vel.velocity_15s || 0);



  // 3. JEV AI EXECUTIVE SIGNAL DASHBOARD

  if (dec) {

    const elHeroCard = document.getElementById('jevHeroActionCard');

    const elHeroAction = document.getElementById('jevActionMain');

    const elHeroSub = document.getElementById('jevActionSub');



    const act = dec.action || 'PASS';

    if (elHeroAction) {

      if (act === 'BUY_UP') {

        elHeroAction.innerHTML = '<span style="color:var(--emerald);">▲ BUY UP CONTRACT</span>';

        if (elHeroCard) elHeroCard.style.borderColor = 'rgba(16, 185, 129, 0.4)';

        if (elHeroSub) elHeroSub.textContent = `Bullish delta +$${(mkt.btc_impulse || 0).toFixed(1)} | Entry target ≤ $${(mkt.up_ask || 0.55).toFixed(2)}`;

      } else if (act === 'BUY_DOWN') {

        elHeroAction.innerHTML = '<span style="color:var(--rose);">▼ BUY DOWN CONTRACT</span>';

        if (elHeroCard) elHeroCard.style.borderColor = 'rgba(244, 63, 94, 0.4)';

        if (elHeroSub) elHeroSub.textContent = `Bearish delta -$${Math.abs(mkt.btc_impulse || 0).toFixed(1)} | Entry target ≤ $${(mkt.dn_ask || 0.55).toFixed(2)}`;

      } else {

        elHeroAction.innerHTML = '<span style="color:var(--text-muted);">⏸ PASS (HOLD CAPITAL)</span>';

        if (elHeroCard) elHeroCard.style.borderColor = 'rgba(255, 255, 255, 0.1)';

        if (elHeroSub) elHeroSub.textContent = 'Market structure choppy or unconfirmed; awaiting higher EV edge';

      }

    }



    // Conviction & Confidence

    const elScore = document.getElementById('jevScoreNum');

    if (elScore) elScore.textContent = (dec.conviction_score || 3.0).toFixed(1);



    const confPct = Math.round((dec.confidence || 0.5) * 100);

    const elConfFill = document.getElementById('jevConfidenceFill');

    if (elConfFill) elConfFill.style.width = `${confPct}%`;



    const elConfBadge = document.getElementById('jevConfidenceBadge');

    if (elConfBadge) elConfBadge.textContent = `Confidence: ${confPct}% (Min Gate: 65%)`;



    // Reversal Trap

    const trapPct = Math.round((dec.reversal_probability || 0.1) * 100);

    const elTrapVal = document.getElementById('jevTrapRiskVal');

    if (elTrapVal) {

      elTrapVal.textContent = `${trapPct}%`;

      elTrapVal.style.color = trapPct > 35 ? 'var(--rose)' : (trapPct > 25 ? 'var(--amber)' : 'var(--emerald)');

    }



    const elTrapBadge = document.getElementById('jevTrapRiskBadge');

    if (elTrapBadge) {

      if (trapPct > 35) elTrapBadge.textContent = 'High Exhaustion Alert';

      else if (trapPct > 25) elTrapBadge.textContent = 'Moderate Resistance';

      else elTrapBadge.textContent = 'Safe Entry Zone';

    }



    const elTrapFill = document.getElementById('jevTrapFill');

    if (elTrapFill) elTrapFill.style.width = `${trapPct}%`;



    // Regime Matrix

    const regProbs = dec.regime_probabilities || {};

    const setRegCell = (cellId, barId, txtId, val, active) => {

      const cell = document.getElementById(cellId);

      const b = document.getElementById(barId);

      const t = document.getElementById(txtId);

      const p = Math.round((val || 0) * 100);

      if (cell) cell.classList.toggle('active-regime', active);

      if (b) b.style.width = `${p}%`;

      if (t) t.textContent = `${p}%`;

    };



    const activeReg = dec.regime || 'CHOPPY_NOISE';

    setRegCell('regimeCellTrend', 'barTrend', 'txtTrendProb', regProbs.STRONG_TREND, activeReg === 'STRONG_TREND');

    setRegCell('regimeCellMispricing', 'barMispricing', 'txtMispricingProb', regProbs.CLOB_MISPRICING, activeReg === 'CLOB_MISPRICING');

    setRegCell('regimeCellStall', 'barStall', 'txtStallProb', regProbs.EXHAUSTION_STALL, activeReg === 'EXHAUSTION_STALL');

    setRegCell('regimeCellChop', 'barChop', 'txtChopProb', regProbs.CHOPPY_NOISE, activeReg === 'CHOPPY_NOISE');



    const elRegBadge = document.getElementById('jevRegimeBadge');

    if (elRegBadge) elRegBadge.textContent = activeReg.replace('_', ' ');



    // Reasoning

    const elReason = document.getElementById('jevDecisionReason');

    if (elReason) elReason.textContent = dec.reason || 'Scanning...';



    // Dynamic sizing summary

    const elDynStake = document.getElementById('jevDynamicStakeVal');

    if (elDynStake) elDynStake.textContent = `$${(dec.dynamic_stake || 5.0).toFixed(2)} Sized Stake`;



    const elDynTp = document.getElementById('jevDynamicTpVal');

    if (elDynTp) elDynTp.textContent = `+${((dec.dynamic_take_profit_pct || 0.18) * 100).toFixed(1)}% Take Profit`;



    // Append to ledger stream if timestamp updated

    if (dec.timestamp_utc && dec.timestamp_utc !== jevLastDecisionTimestamp) {

      jevLastDecisionTimestamp = dec.timestamp_utc;

      appendDecisionToTable(dec, mkt, vel);

    }

  }



  // 4. CLOB ORDER BOOK LADDER

  const elUpAsk = document.getElementById('bookUpAsk');

  const elUpBid = document.getElementById('bookUpBid');

  const elDnAsk = document.getElementById('bookDnAsk');

  const elDnBid = document.getElementById('bookDnBid');



  if (elUpAsk) elUpAsk.textContent = mkt.up_ask ? `$${mkt.up_ask.toFixed(2)}` : '$--';

  if (elUpBid) elUpBid.textContent = mkt.up_bid ? `$${mkt.up_bid.toFixed(2)}` : '$--';

  if (elDnAsk) elDnAsk.textContent = mkt.dn_ask ? `$${mkt.dn_ask.toFixed(2)}` : '$--';

  if (elDnBid) elDnBid.textContent = mkt.dn_bid ? `$${mkt.dn_bid.toFixed(2)}` : '$--';



  const elSpUp = document.getElementById('jevSpreadUpVal');

  const elSpDn = document.getElementById('jevSpreadDnVal');

  if (elSpUp) elSpUp.textContent = mkt.spread_up !== null && mkt.spread_up !== undefined ? `$${mkt.spread_up.toFixed(2)}` : '--';

  if (elSpDn) elSpDn.textContent = mkt.spread_down !== null && mkt.spread_down !== undefined ? `$${mkt.spread_down.toFixed(2)}` : '--';



  // 5. BOT EXECUTION CONTROLS

  const elDot = document.getElementById('jevStatusDot');

  const elText = document.getElementById('jevStatusText');

  const btnStart = document.getElementById('btnStartJevBot');

  const btnStop = document.getElementById('btnStopJevBot');

  const elPid = document.getElementById('jevPidInfo');



  if (isRunning) {

    if (elDot) elDot.className = 'status-dot dot-running';

    if (elText) elText.textContent = `Jev Bot: RUNNING [PID ${botInfo.pid || '--'}] (${botInfo.mode || 'paper'})`;

    if (btnStart) btnStart.disabled = true;

    if (btnStop) btnStop.disabled = false;

    if (elPid) elPid.textContent = `Active Candle: ${mkt.slug ? mkt.slug.slice(-10) : '--'} | Started: ${botInfo.started_at || '--'}`;

  } else {

    if (elDot) elDot.className = 'status-dot dot-stopped';

    if (elText) elText.textContent = 'Jev Bot: IDLE / READY';

    if (btnStart) btnStart.disabled = false;

    if (btnStop) btnStop.disabled = true;

    if (elPid) elPid.textContent = 'No active background PID';

  }

}



// Visual Velocity Thrust Meter

function updateVelocityThrust(vel15) {

  const indicator = document.getElementById('jevVelocityIndicator');

  if (!indicator) return;

  // Map -20 $/s to +20 $/s to 5% - 95%

  const clamped = Math.max(-20, Math.min(20, vel15));

  const pct = 50 + (clamped / 20) * 45;

  indicator.style.left = `${pct}%`;

  indicator.style.background = clamped > 1 ? 'var(--emerald)' : (clamped < -1 ? 'var(--rose)' : '#fff');

  indicator.style.boxShadow = clamped > 1 ? '0 0 10px var(--emerald)' : (clamped < -1 ? '0 0 10px var(--rose)' : '0 0 8px #fff');

}



// Real-Time Canvas Impulse Chart

function updateImpulseChart(slug, impulseVal) {

  const canvas = document.getElementById('jevImpulseCanvas');

  if (!canvas) return;



  // Handle candle roll

  if (slug && slug !== jevLastCandleSlug) {

    jevLastCandleSlug = slug;

    jevImpulseHistory = [];

  }



  jevImpulseHistory.push(impulseVal);

  if (jevImpulseHistory.length > 120) {

    jevImpulseHistory.shift();

  }



  const ctx = canvas.getContext('2d');

  const w = canvas.width;

  const h = canvas.height;

  ctx.clearRect(0, 0, w, h);



  // Background Grid Lines

  ctx.strokeStyle = 'rgba(255, 255, 255, 0.04)';

  ctx.lineWidth = 1;

  for (let y = 30; y < h; y += 35) {

    ctx.beginPath();

    ctx.moveTo(0, y);

    ctx.lineTo(w, y);

    ctx.stroke();

  }



  // Zero Baseline in Center

  const midY = h / 2;

  ctx.strokeStyle = 'rgba(255, 255, 255, 0.2)';

  ctx.setLineDash([4, 4]);

  ctx.beginPath();

  ctx.moveTo(0, midY);

  ctx.lineTo(w, midY);

  ctx.stroke();

  ctx.setLineDash([]);



  // Baseline Label

  ctx.fillStyle = 'rgba(255, 255, 255, 0.35)';

  ctx.font = '10px monospace';

  ctx.fillText('5M OPEN BASELINE ($0.00)', 10, midY - 6);



  if (jevImpulseHistory.length < 2) return;



  // Dynamic Scale

  let maxDelta = 35.0;

  jevImpulseHistory.forEach(v => {

    if (Math.abs(v) > maxDelta) maxDelta = Math.abs(v);

  });

  maxDelta *= 1.15; // padding



  const stepX = w / (Math.max(jevImpulseHistory.length - 1, 30));



  // Draw Impulse Area Fill

  const lastVal = jevImpulseHistory[jevImpulseHistory.length - 1];

  const isBull = lastVal >= 0;



  ctx.beginPath();

  ctx.moveTo(0, midY);

  jevImpulseHistory.forEach((v, i) => {

    const x = i * stepX;

    const y = midY - (v / maxDelta) * (midY - 20);

    ctx.lineTo(x, y);

  });

  ctx.lineTo((jevImpulseHistory.length - 1) * stepX, midY);

  ctx.closePath();



  const grad = ctx.createLinearGradient(0, 0, 0, h);

  if (isBull) {

    grad.addColorStop(0, 'rgba(16, 185, 129, 0.3)');

    grad.addColorStop(0.5, 'rgba(16, 185, 129, 0.05)');

    grad.addColorStop(1, 'transparent');

  } else {

    grad.addColorStop(0, 'transparent');

    grad.addColorStop(0.5, 'rgba(244, 63, 94, 0.05)');

    grad.addColorStop(1, 'rgba(244, 63, 94, 0.3)');

  }

  ctx.fillStyle = grad;

  ctx.fill();



  // Draw Impulse Line

  ctx.beginPath();

  jevImpulseHistory.forEach((v, i) => {

    const x = i * stepX;

    const y = midY - (v / maxDelta) * (midY - 20);

    if (i === 0) ctx.moveTo(x, y);

    else ctx.lineTo(x, y);

  });

  ctx.strokeStyle = isBull ? '#10b981' : '#f43f5e';

  ctx.lineWidth = 2.5;

  ctx.stroke();



  // Current Price Dot

  const curX = (jevImpulseHistory.length - 1) * stepX;

  const curY = midY - (lastVal / maxDelta) * (midY - 20);



  ctx.beginPath();

  ctx.arc(curX, curY, 5, 0, 2 * Math.PI);

  ctx.fillStyle = '#fff';

  ctx.fill();

  ctx.strokeStyle = isBull ? '#10b981' : '#f43f5e';

  ctx.lineWidth = 2;

  ctx.stroke();

}



function renderCanvasGrid() {

  const canvas = document.getElementById('jevImpulseCanvas');

  if (canvas) updateImpulseChart(jevLastCandleSlug, 0);

}



// Append Decision to Ledger Table

function appendDecisionToTable(dec, mkt, vel) {

  const tbody = document.getElementById('jevDecisionFeedList');

  if (!tbody) return;



  const placeholder = tbody.querySelector('.feed-placeholder-row');

  if (placeholder) placeholder.remove();



  const timeStr = (dec.timestamp_utc || new Date().toISOString()).split('T')[1].replace('Z', '');

  const act = dec.action || 'PASS';

  let actPill = '<span class="pill-action-pass">PASS</span>';

  if (act === 'BUY_UP') actPill = '<span class="pill-action-buy-up">BUY UP</span>';

  else if (act === 'BUY_DOWN') actPill = '<span class="pill-action-buy-down">BUY DOWN</span>';



  const imp = mkt.btc_impulse !== undefined ? (mkt.btc_impulse >= 0 ? `+$${mkt.btc_impulse.toFixed(1)}` : `-$${Math.abs(mkt.btc_impulse).toFixed(1)}`) : '--';

  const v15 = vel.velocity_15s !== undefined ? (vel.velocity_15s >= 0 ? `+${vel.velocity_15s.toFixed(1)}` : `${vel.velocity_15s.toFixed(1)}`) : '--';



  const row = document.createElement('tr');

  row.innerHTML = `

    <td>${timeStr}</td>

    <td>${actPill}</td>

    <td><span class="tag-regime">${(dec.regime || 'CHOP').replace('_', ' ')}</span></td>

    <td><strong>${Math.round((dec.confidence || 0.5) * 100)}%</strong></td>

    <td style="color:${mkt.btc_impulse >= 0 ? 'var(--emerald)' : 'var(--rose)'}">${imp}</td>

    <td>${v15} $/s</td>

    <td>UP $${(mkt.up_ask || 0).toFixed(2)} / DN $${(mkt.dn_ask || 0).toFixed(2)}</td>

    <td>$${(dec.dynamic_stake || 5.0).toFixed(2)}</td>

    <td style="max-width:320px; white-space:nowrap; overflow:hidden; text-overflow:ellipsis;" title="${dec.reason || ''}">${dec.reason || '--'}</td>

  `;



  tbody.insertBefore(row, tbody.firstChild);



  // Keep top 30

  while (tbody.children.length > 30) {

    tbody.removeChild(tbody.lastChild);

  }

}



// Bot Control Actions

async function startJevAutonomousBot() {

  const isLive = document.getElementById('jevModeLive') && document.getElementById('jevModeLive').classList.contains('active');

  const baseStake = parseFloat(document.getElementById('jevBaseStakeInput').value) || 5.0;

  const maxStake = parseFloat(document.getElementById('jevMaxStakeInput').value) || 10.0;

  const minConf = parseInt(document.getElementById('jevMinConfSlider').value) || 65;

  const maxTrap = parseInt(document.getElementById('jevMaxTrapSlider').value) || 35;



  const btnStart = document.getElementById('btnStartJevBot');

  if (btnStart) btnStart.disabled = true;



  try {

    const res = await fetch('/api/bot/start', {

      method: 'POST',

      headers: { 'Content-Type': 'application/json' },

      body: JSON.stringify({

        profile: 'jev_ai_brain',

        mode: isLive ? 'real' : 'paper',

        execute: isLive,

        use_jev: true,

        stake: baseStake,

        stake_usd: baseStake,

        max_stake: maxStake,

        min_conf: minConf,

        max_trap: maxTrap,

        run_mode: 'continuous_loop'

      })

    });

    const data = await res.json();

    if (data.status === 'ok' || data.success) {

      alert(`Jev Autonomous Bot successfully launched in ${isLive ? 'REAL' : 'PAPER'} mode!`);

      if (typeof fetchJevTelemetry === 'function') fetchJevTelemetry();

    } else {

      alert(`Could not start bot: ${data.message || data.error || 'Unknown error'}`);

      if (btnStart) btnStart.disabled = false;

    }

  } catch (e) {

    alert(`Error starting bot: ${e}`);

    if (btnStart) btnStart.disabled = false;

  }

}



async function stopJevAutonomousBot() {

  const btnStop = document.getElementById('btnStopJevBot');

  if (btnStop) btnStop.disabled = true;



  try {

    const res = await fetch('/api/bot/stop', { method: 'POST' });

    const data = await res.json();

    alert('Jev Autonomous Bot stopped cleanly.');

    if (typeof fetchJevTelemetry === 'function') fetchJevTelemetry();

  } catch (e) {

    alert(`Error stopping bot: ${e}`);

  }

}



async function emergencyHaltJevBot() {

  if (confirm('EMERGENCY: Immediately terminate Jev Bot process and cancel all live orders?')) {

    await stopJevAutonomousBot();

  }

}



async function testJevBrainSingleTick() {

  const btn = document.getElementById('btnTestJevDecision');

  if (btn) btn.textContent = '🔄 Querying Jev API...';

  try {

    await fetchJevTelemetry();

  } finally {

    if (btn) btn.textContent = '🔄 Query Jev Brain Now (Single Tick Analysis)';

  }

}





// ==========================================================================

// JEV AI DIRECTIONAL LONG & SHORT TERMINAL CONTROLLER

// ==========================================================================



let longShortPollInterval = null;

let lastLongShortTimestamp = null;

let currentLongShortData = null;



let calcRiskPct = 2.0;

let calcLeverage = 10;



window.setCalcRisk = function(pct) {

  calcRiskPct = parseFloat(pct); currentRiskPct = calcRiskPct;

  const lbl = document.getElementById('calcRiskPctLabel');

  if (lbl) lbl.textContent = `${calcRiskPct.toFixed(1)}%`;



  document.querySelectorAll('.stake-pill').forEach(btn => {

    if (btn.getAttribute('onclick') && btn.getAttribute('onclick').includes('setCalcRisk')) {

      btn.classList.toggle('active', btn.textContent.trim() === `${pct}%`);

    }

  });

  recalculatePositionSize();

};



window.setCalcLev = function(lev) {

  calcLeverage = parseInt(lev); currentLeverage = calcLeverage;

  const lbl = document.getElementById('calcLeverageLabel');

  if (lbl) lbl.textContent = `${calcLeverage}x`;

  const slider = document.getElementById('calcLeverageSlider');

  if (slider) slider.value = calcLeverage;



  document.querySelectorAll('.stake-pill').forEach(btn => {

    if (btn.getAttribute('onclick') && btn.getAttribute('onclick').includes('setCalcLev')) {

      btn.classList.toggle('active', btn.textContent.trim() === `${lev}x`);

    }

  });

  recalculatePositionSize();

};



function recalculatePositionSize() {

  const balInput = document.getElementById('calcBalanceInput');

  const bal = parseFloat(balInput ? balInput.value : 1000) || 1000;

  const spot = (currentLongShortData && currentLongShortData.btc_spot) ? currentLongShortData.btc_spot : 81250.0;

  const slDist = (currentLongShortData && currentLongShortData.sl_distance_usd) ? currentLongShortData.sl_distance_usd : 120.0;

  const tp1Dist = (currentLongShortData && currentLongShortData.tp1_distance_usd) ? currentLongShortData.tp1_distance_usd : 240.0;

  const tp2Dist = tp1Dist * 2.0;



  // Max dollar risk

  const maxRiskUsd = bal * (calcRiskPct / 100.0);

  const slPct = Math.max(0.001, slDist / spot);



  // Position size based on risk

  let posUsd = maxRiskUsd / slPct;

  const maxAllowedUsd = bal * calcLeverage;

  if (posUsd > maxAllowedUsd) {

    posUsd = maxAllowedUsd;

  }



  const posBtc = posUsd / spot;

  const marginReq = posUsd / calcLeverage;

  const liqBuffer = Math.max(1.0, (100.0 / calcLeverage) * 0.9);



  const profitTp1 = posUsd * (tp1Dist / spot);

  const profitTp2 = posUsd * (tp2Dist / spot);

  const roe1 = (profitTp1 / marginReq) * 100;

  const roe2 = (profitTp2 / marginReq) * 100;



  const elRisk = document.getElementById('calcMaxRiskDollars');

  if (elRisk) elRisk.textContent = `$${maxRiskUsd.toFixed(2)}`;



  const elPosUsd = document.getElementById('calcPositionUsd');

  if (elPosUsd) elPosUsd.textContent = `$${posUsd.toLocaleString(undefined, {minimumFractionDigits: 2, maximumFractionDigits: 2})}`;



  const elPosBtc = document.getElementById('calcPositionBtc');

  if (elPosBtc) elPosBtc.textContent = `${posBtc.toFixed(4)} BTC`;



  const elMargin = document.getElementById('calcMarginReq');

  if (elMargin) elMargin.textContent = `$${marginReq.toFixed(2)}`;



  const elLiq = document.getElementById('calcLiqDistance');

  if (elLiq) elLiq.textContent = `-${liqBuffer.toFixed(1)}% Liquidation Buffer`;



  const elTp1 = document.getElementById('calcProfitTp1');

  if (elTp1) elTp1.textContent = `+$${profitTp1.toFixed(2)} (+${roe1.toFixed(0)}% ROE)`;



  const elTp2 = document.getElementById('calcProfitTp2');

  if (elTp2) elTp2.textContent = `+$${profitTp2.toFixed(2)} (+${roe2.toFixed(0)}% ROE)`;

}



window.copySignalSetup = function() {

  if (!currentLongShortData) {

    alert('Awaiting signal data to copy.');

    return;

  }

  const c = currentLongShortData;

  const text = `🎯 JEV AI CRYPTO SIGNAL: ${c.signal_label}\n` +

    `Symbol: BTC/USDT (Spot / Perp)\n` +

    `Current Spot: $${c.btc_spot.toLocaleString()}\n` +

    `Entry Zone: $${c.entry_zone[0].toLocaleString()} - $${c.entry_zone[1].toLocaleString()}\n` +

    `Stop Loss: $${c.stop_loss.toLocaleString()} (-$${c.sl_distance_usd})\n` +

    `Take Profit 1: $${c.take_profit_1.toLocaleString()} (+$${c.tp1_distance_usd})\n` +

    `Take Profit 2: $${c.take_profit_2.toLocaleString()}\n` +

    `Risk / Reward: ${c.risk_reward_ratio}\n` +

    `Conviction Score: ${c.conviction_score} / 5.0 (${Math.round(c.confidence * 100)}% Conf)\n` +

    `Rationale: ${c.status_description}`;



  navigator.clipboard.writeText(text).then(() => {

    alert('Signal setup copied to clipboard!');

  }).catch(() => {

    alert('Copied:\n' + text);

  });

};



function startLongShortPolling() {

  if (longShortPollInterval) clearInterval(longShortPollInterval);

  fetchJevLongShortTelemetry();

  fetchDirectionalPosition();

  fetchCandleData();

  longShortPollInterval = setInterval(() => {

    fetchJevLongShortTelemetry();

    fetchDirectionalPosition();

  }, 1000);

  if (!candlePollInterval) {

    candlePollInterval = setInterval(fetchCandleData, 2500);

  }

  window.addEventListener("resize", renderCandleChart);

}



async function fetchJevLongShortTelemetry() {

  try {

    const res = await fetch('/api/jev/long-short');

    if (!res.ok) return;

    const data = await res.json();

    renderLongShortTerminal(data);

  } catch (e) {

    // console.warn('Long/Short polling error:', e);

  }

}



function renderLongShortTerminal(data) {

  const call = data.current_call || {};

  currentLongShortData = call;



  // 1. Top Ticker

  const elSpot = document.getElementById('lsSpotPrice');

  if (elSpot) {

    elSpot.textContent = call.btc_spot ? `$${call.btc_spot.toLocaleString(undefined, {minimumFractionDigits: 1, maximumFractionDigits: 1})}` : '$---,---.-';

  }



  const elImp = document.getElementById('lsImpulseDelta');

  if (elImp && call.impulse !== undefined) {

    const sign = call.impulse >= 0 ? '+' : '';

    elImp.textContent = `${sign}$${call.impulse.toFixed(1)}`;

    elImp.style.color = call.impulse >= 0 ? 'var(--emerald)' : 'var(--rose)';

  }



  const elOpen = document.getElementById('lsOpenRef');

  if (elOpen) {

    elOpen.textContent = call.btc_open ? `5M Open: $${call.btc_open.toLocaleString(undefined, {minimumFractionDigits: 1, maximumFractionDigits: 1})}` : '5M Open: $---,---';

  }



  const elVel = document.getElementById('lsVelocity');

  if (elVel) {

    const v15 = call.velocity_15s || 0;

    elVel.textContent = `${v15 >= 0 ? '+' : ''}${v15.toFixed(1)} $/s`;

    elVel.style.color = v15 >= 0 ? 'var(--emerald)' : 'var(--rose)';

  }



  const elAcc = document.getElementById('lsAcceleration');

  if (elAcc) {

    const acc = call.acceleration || 0;

    elAcc.textContent = `${acc >= 0 ? '+' : ''}${acc.toFixed(2)} $/s²`;

    elAcc.style.color = acc >= 0 ? 'var(--cyan)' : 'var(--amber)';

  }



  const elReg = document.getElementById('lsRegimeTag');

  if (elReg) {

    elReg.textContent = (call.regime || 'CHOPPY_NOISE').replace('_', ' ');

  }



  const elBias = document.getElementById('lsBiasBadge');

  if (elBias) {

    elBias.textContent = call.signal_type || 'NEUTRAL';

    if (call.signal_type === 'LONG') {

      elBias.style.color = 'var(--emerald)';

    } else if (call.signal_type === 'SHORT') {

      elBias.style.color = 'var(--rose)';

    } else {

      elBias.style.color = 'var(--cyan)';

    }

  }



  // Institutional Flow Metrics

  const elOi = document.getElementById('lsOpenInterest');

  if (elOi && call.oi_usd_formatted) {

    const deltaStr = call.oi_5m_delta_formatted || '+$0.0M';

    const isDeltaPositive = !deltaStr.startsWith('-');

    const deltaColor = isDeltaPositive ? 'var(--emerald)' : 'var(--rose)';

    elOi.innerHTML = `${call.oi_usd_formatted} <span style="font-size:10px; color:${deltaColor};" id="lsOiDelta">(${deltaStr})</span>`;

  }



  const elCbPrem = document.getElementById('lsCoinbasePremium');

  if (elCbPrem && call.coinbase_premium !== undefined) {

    const prem = call.coinbase_premium;

    const sign = prem >= 0 ? '+' : '';

    elCbPrem.textContent = `${sign}$${prem.toFixed(2)}`;

    elCbPrem.style.color = prem >= 0 ? 'var(--emerald)' : 'var(--rose)';

  }



  const elFunding = document.getElementById('lsFundingRate');

  if (elFunding && call.funding_rate_pct !== undefined) {

    const fund = call.funding_rate_pct;

    const sign = fund >= 0 ? '+' : '';

    elFunding.textContent = `${sign}${fund.toFixed(4)}%`;

    elFunding.style.color = fund >= 0 ? 'var(--cyan)' : 'var(--amber)';

  }



  // Multi-Timeframe Technical Radar (4H, 8H, 12H, 1W, 15M, 1M)

  const mtf = call.mtf || {};

  const tfs = mtf.timeframes || {};



  const bindTfBadge = (elId, tfKey, label) => {

    const el = document.getElementById(elId);

    if (!el) return;

    const tfInfo = tfs[tfKey] || {};

    const trend = tfInfo.trend || 'NEUTRAL';

    el.textContent = `${label}: ${trend}`;

    if (trend === 'BULLISH') {

      el.style.color = '#10b981';

      el.style.borderColor = 'rgba(16,185,129,0.4)';

      el.style.background = 'rgba(16,185,129,0.12)';

    } else if (trend === 'BEARISH') {

      el.style.color = '#f43f5e';

      el.style.borderColor = 'rgba(244,63,94,0.4)';

      el.style.background = 'rgba(244,63,94,0.12)';

    } else {

      el.style.color = 'var(--text-muted)';

      el.style.borderColor = 'rgba(100,116,139,0.3)';

      el.style.background = 'rgba(30,41,59,0.7)';

    }

  };



  bindTfBadge('mtfBadge1m', '1m', '1M');

  bindTfBadge('mtfBadge15m', '15m', '15M');

  bindTfBadge('mtfBadge4h', '4h', '4H');

  bindTfBadge('mtfBadge8h', '8h', '8H');

  bindTfBadge('mtfBadge12h', '12h', '12H');

  bindTfBadge('mtfBadge1w', '1w', '1W');



  const elRsi4h = document.getElementById('mtfRsi4h');

  if (elRsi4h && tfs['4h'] && tfs['4h'].rsi !== undefined) {

    const rsi = tfs['4h'].rsi;

    elRsi4h.textContent = rsi.toFixed(1);

    elRsi4h.style.color = rsi > 70 ? 'var(--amber)' : rsi < 30 ? 'var(--cyan)' : 'var(--emerald)';

  }



  const elBoll4h = document.getElementById('mtfBollinger4h');

  if (elBoll4h && tfs['4h'] && tfs['4h'].bollinger) {

    const b = tfs['4h'].bollinger;

    elBoll4h.textContent = `%B: ${b.pct_b.toFixed(2)}`;

    elBoll4h.style.color = b.squeeze ? 'var(--amber)' : 'var(--text-muted)';

  }



  const elAtr4h = document.getElementById('mtfAtr4h');

  if (elAtr4h && tfs['4h'] && tfs['4h'].atr !== undefined) {

    elAtr4h.textContent = `±$${Math.round(tfs['4h'].atr)}`;

  }



  const elAuthPill = document.getElementById('lsAutonomousAuthorityPill');

  if (elAuthPill) {

    const lev = call.dynamic_leverage || 5;

    const risk = call.dynamic_risk_pct || 1.5;

    const slDist = call.sl_distance_usd || 500;

    elAuthPill.innerHTML = `<span>🤖 Jev Authority: <strong>${lev}x Lev</strong> • <strong>${risk}% Risk</strong> • Safe SL <strong>±$${Math.round(slDist)}</strong></span>`;

  }



  // Global Macro & Session Clock Bar Update

  const elGold = document.getElementById('macroGoldBadge');

  if (elGold && call.gold_usd !== undefined) {

    const chg = call.gold_change_24h_pct || 0;

    elGold.textContent = `Gold: $${call.gold_usd.toLocaleString(undefined, {minimumFractionDigits: 1, maximumFractionDigits: 1})} (${chg >= 0 ? '+' : ''}${chg.toFixed(2)}%)`;

    elGold.style.color = chg > 0 ? 'var(--emerald)' : chg < 0 ? '#f43f5e' : 'var(--text-muted)';

  }



  const elEthBtc = document.getElementById('macroEthBtcBadge');

  if (elEthBtc && call.eth_btc !== undefined) {

    const chg = call.eth_btc_change_24h_pct || 0;

    elEthBtc.textContent = `ETH/BTC: ${call.eth_btc.toFixed(5)} (${chg >= 0 ? '+' : ''}${chg.toFixed(2)}%)`;

    elEthBtc.style.color = chg > 0.5 ? 'var(--emerald)' : chg < -0.5 ? '#f43f5e' : 'var(--text-muted)';

  }



  const elSolBtc = document.getElementById('macroSolBtcBadge');

  if (elSolBtc && call.sol_btc !== undefined) {

    const chg = call.sol_btc_change_24h_pct || 0;

    elSolBtc.textContent = `SOL/BTC: ${call.sol_btc.toFixed(5)} (${chg >= 0 ? '+' : ''}${chg.toFixed(2)}%)`;

  }



  const elMacroReg = document.getElementById('macroRegimeBadge');

  if (elMacroReg && call.macro_risk_regime) {

    elMacroReg.textContent = `MACRO: ${call.macro_risk_regime.replace(/_/g, ' ')}`;

    if (call.macro_risk_regime === 'MACRO_RISK_ON') {

      elMacroReg.style.color = 'var(--emerald)';

      elMacroReg.style.borderColor = 'rgba(16,185,129,0.5)';

      elMacroReg.style.background = 'rgba(16,185,129,0.18)';

    } else if (call.macro_risk_regime.includes('DE_RISKING')) {

      elMacroReg.style.color = '#f43f5e';

      elMacroReg.style.borderColor = 'rgba(244,63,94,0.5)';

      elMacroReg.style.background = 'rgba(244,63,94,0.18)';

    } else {

      elMacroReg.style.color = 'var(--cyan)';

      elMacroReg.style.borderColor = 'rgba(6,182,212,0.4)';

      elMacroReg.style.background = 'rgba(6,182,212,0.12)';

    }

  }



  const elSession = document.getElementById('sessionClockBadge');

  if (elSession) {

    elSession.textContent = call.session_label || call.session_name || 'SESSION ACTIVE';

  }



  const elMagnet = document.getElementById('liquidityMagnetBadge');

  if (elMagnet) {

    elMagnet.textContent = call.magnet_status || 'Scanning Liquidity Pools...';

    if (call.magnet_status && call.magnet_status.includes('MAGNET ACTIVE')) {

      elMagnet.style.color = 'var(--amber)';

      elMagnet.style.borderColor = 'rgba(245,158,11,0.5)';

      elMagnet.style.background = 'rgba(245,158,11,0.18)';

    } else {

      elMagnet.style.color = 'var(--text-muted)';

      elMagnet.style.borderColor = 'rgba(100,116,139,0.3)';

      elMagnet.style.background = 'rgba(30,41,59,0.7)';

    }

  }



  // Quant Math & Order Flow Radar Update

  const elHurst = document.getElementById('quantHurstBadge');

  if (elHurst && call.hurst_exponent !== undefined) {

    const h = call.hurst_exponent;

    const reg = call.hurst_regime || 'CHOP';

    elHurst.textContent = `Hurst: ${h.toFixed(3)} (${reg})`;

    if (call.is_random_walk) {

      elHurst.style.color = '#f43f5e';

      elHurst.style.borderColor = 'rgba(244,63,94,0.5)';

      elHurst.style.background = 'rgba(244,63,94,0.18)';

    } else if (reg.includes('TREND')) {

      elHurst.style.color = 'var(--emerald)';

      elHurst.style.borderColor = 'rgba(16,185,129,0.5)';

      elHurst.style.background = 'rgba(16,185,129,0.15)';

    } else {

      elHurst.style.color = 'var(--cyan)';

      elHurst.style.borderColor = 'rgba(6,182,212,0.4)';

      elHurst.style.background = 'rgba(6,182,212,0.12)';

    }

  }



  const elOu = document.getElementById('quantOuBadge');

  if (elOu) {

    const tau = Math.round(call.ou_half_life_min || 0);

    elOu.textContent = `O-U: ${tau > 0 && tau < 900 ? tau + 'm Half-Life' : 'Trending'}`;

  }



  const elCvd = document.getElementById('quantCvdBadge');

  if (elCvd) {

    const perp = Math.round(call.perp_cvd_15m || 0);

    const spot = Math.round(call.spot_cvd_15m || 0);

    elCvd.textContent = `CVD 15M: ${perp >= 0 ? '+' : ''}${perp} BTC (Perp) / ${spot >= 0 ? '+' : ''}${spot} (Spot)`;

    elCvd.style.color = perp > 25 ? 'var(--emerald)' : perp < -25 ? '#f43f5e' : 'var(--text-muted)';

  }



  // Smart Maker Execution & Fee Savings

  const elFeeSaved = document.getElementById('feeSavedBadge');

  if (elFeeSaved && call.cumulative_fees_saved !== undefined) {

    elFeeSaved.textContent = `Fees Saved: +$${call.cumulative_fees_saved.toFixed(2)}`;

  }



  // Dynamic Strategy Mode (Trend vs Mean-Reversion Scalper)

  const elStratMode = document.getElementById('strategyModeBadge');

  if (elStratMode && call.strategy_mode) {

    const mode = call.strategy_mode;

    if (mode === 'MEAN_REVERSION_SCALPER') {

      elStratMode.textContent = `🎯 MODE: MEAN-REVERSION SCALPER`;

      elStratMode.style.color = '#fbbf24';

      elStratMode.style.borderColor = 'rgba(251,191,36,0.6)';

      elStratMode.style.background = 'rgba(251,191,36,0.18)';

    } else {

      elStratMode.textContent = `⚡ MODE: TREND EXPANSION`;

      elStratMode.style.color = 'var(--blue)';

      elStratMode.style.borderColor = 'rgba(59,130,246,0.4)';

      elStratMode.style.background = 'rgba(59,130,246,0.15)';

    }

  }



  // Coinbase Lead-Lag Arbitrage

  const elCbLead = document.getElementById('cbLeadBadge');

  if (elCbLead && call.coinbase_lead_lag_label) {

    const vel = call.coinbase_velocity_delta || 0.0;

    elCbLead.textContent = `🇺🇸 CB Lead: ${call.coinbase_lead_lag_label} (${vel >= 0 ? '+' : ''}${vel.toFixed(1)} $/s)`;

    if (call.coinbase_lead_lag_bias === 'COINBASE_SPOT_LEADING_BULLISH') {

      elCbLead.style.color = 'var(--emerald)';

      elCbLead.style.borderColor = 'rgba(16,185,129,0.5)';

      elCbLead.style.background = 'rgba(16,185,129,0.15)';

    } else if (call.coinbase_lead_lag_bias === 'COINBASE_SPOT_LEADING_BEARISH') {

      elCbLead.style.color = '#f43f5e';

      elCbLead.style.borderColor = 'rgba(244,63,94,0.5)';

      elCbLead.style.background = 'rgba(244,63,94,0.15)';

    } else {

      elCbLead.style.color = 'var(--cyan)';

      elCbLead.style.borderColor = 'rgba(6,182,212,0.4)';

      elCbLead.style.background = 'rgba(6,182,212,0.12)';

    }

  }



  // Episodic Memory Lesson

  const elMem = document.getElementById('memoryLessonBadge');

  if (elMem && call.memory_lesson) {

    elMem.textContent = `🧠 Memory: ${call.memory_lesson}`;

    elMem.title = call.memory_lesson;

  }



  // L2 Order Book Depth & Whale Walls

  const elObi = document.getElementById('obObiBadge');

  if (elObi && call.order_book_obi !== undefined) {

    const obiPct = (call.order_book_obi * 100);

    const bias = (call.obi_bias || 'BALANCED').replace(/_/g, ' ');

    elObi.textContent = `OBI: ${obiPct >= 0 ? '+' : ''}${obiPct.toFixed(1)}% (${bias})`;

    elObi.style.color = obiPct > 15 ? 'var(--emerald)' : obiPct < -15 ? '#f43f5e' : 'var(--text-muted)';

  }



  const elObDepth = document.getElementById('obDepthBadge');

  if (elObDepth && call.bid_depth_m !== undefined && call.ask_depth_m !== undefined) {

    elObDepth.textContent = `Depth: $${call.bid_depth_m.toFixed(2)}M Bids / $${call.ask_depth_m.toFixed(2)}M Asks`;

  }



  const elWhale = document.getElementById('obWhaleWallBadge');

  if (elWhale) {

    if (call.nearest_ask_wall_price > 0 && call.nearest_ask_wall_btc > 0) {

      elWhale.textContent = `Whale Ask Wall: $${call.nearest_ask_wall_price.toLocaleString()} (${call.nearest_ask_wall_btc.toFixed(0)} BTC)`;

      elWhale.style.color = '#f43f5e';

    } else if (call.nearest_bid_wall_price > 0 && call.nearest_bid_wall_btc > 0) {

      elWhale.textContent = `Whale Bid Wall: $${call.nearest_bid_wall_price.toLocaleString()} (${call.nearest_bid_wall_btc.toFixed(0)} BTC)`;

      elWhale.style.color = 'var(--emerald)';

    } else {

      elWhale.textContent = `Whales: Fluid (No Block Walls)`;

      elWhale.style.color = 'var(--text-muted)';

    }

  }



  // Deribit DVOL Telemetry

  const elDvol = document.getElementById('dvolBadge');

  if (elDvol && call.dvol_index !== undefined) {

    const dvolVal = call.dvol_index;

    const mult = call.dvol_multiplier || 1.0;

    const reg = (call.dvol_regime || '').replace(/_/g, ' ');

    elDvol.textContent = `⚡ DVOL: ${dvolVal.toFixed(1)} (${reg}${mult > 1 ? ` · ${mult}x Target` : ''})`;

    if (call.dvol_regime === 'EXTREME_VOL_SQUEEZE') {

      elDvol.style.color = '#fbbf24';

      elDvol.style.borderColor = 'rgba(251,191,36,0.6)';

      elDvol.style.background = 'rgba(251,191,36,0.18)';

    } else if (call.dvol_regime === 'VOL_EXPANSION_SPIKE') {

      elDvol.style.color = '#f43f5e';

      elDvol.style.borderColor = 'rgba(244,63,94,0.6)';

      elDvol.style.background = 'rgba(244,63,94,0.18)';

    } else {

      elDvol.style.color = 'var(--emerald)';

      elDvol.style.borderColor = 'rgba(16,185,129,0.4)';

      elDvol.style.background = 'rgba(16,185,129,0.12)';

    }

  }



  // Tier-1 Economic News Blackout Shield

  const elNews = document.getElementById('newsShieldBadge');

  if (elNews) {

    if (call.is_news_blackout) {

      elNews.textContent = `🚨 BLACKOUT ACTIVE: ${call.blackout_reason || 'Macro Release'}`;

      elNews.style.color = '#f43f5e';

      elNews.style.borderColor = 'rgba(244,63,94,0.8)';

      elNews.style.background = 'rgba(244,63,94,0.25)';

    } else {

      const up = call.upcoming_news_event ? ` (${call.upcoming_news_event})` : '';

      elNews.textContent = `🛡️ News Shield: Clear${up}`;

      elNews.style.color = 'var(--emerald)';

      elNews.style.borderColor = 'rgba(16,185,129,0.4)';

      elNews.style.background = 'rgba(16,185,129,0.12)';

    }

  }



  const elDiv = document.getElementById('quantDivAlertBadge');

  if (elDiv) {

    const alertTag = call.divergence_alert && call.divergence_alert !== 'NORMAL' ? call.divergence_alert : (call.order_flow_bias || 'BALANCED');

    elDiv.textContent = `Flow: ${alertTag.replace(/_/g, ' ')}`;

    if (alertTag.includes('ABSORPTION')) {

      elDiv.style.color = 'var(--emerald)';

      elDiv.style.borderColor = 'rgba(16,185,129,0.5)';

      elDiv.style.background = 'rgba(16,185,129,0.18)';

    } else if (alertTag.includes('EXHAUSTION') || alertTag.includes('TRAP')) {

      elDiv.style.color = '#f43f5e';

      elDiv.style.borderColor = 'rgba(244,63,94,0.5)';

      elDiv.style.background = 'rgba(244,63,94,0.18)';

    } else {

      elDiv.style.color = 'var(--text-muted)';

      elDiv.style.borderColor = 'rgba(100,116,139,0.3)';

      elDiv.style.background = 'rgba(30,41,59,0.7)';

    }

  }



  const elEv = document.getElementById('quantEvBadge');

  if (elEv) {

    const evVal = call.expected_value_usd || 0;

    const passed = call.ev_hurdle_passed !== false;

    elEv.textContent = `EV: ${evVal >= 0 ? '+' : ''}$${evVal.toFixed(2)} (${passed ? 'PASSED' : 'BLOCKED'})`;

    elEv.style.color = passed ? 'var(--emerald)' : '#f43f5e';

    elEv.style.borderColor = passed ? 'rgba(16,185,129,0.4)' : 'rgba(244,63,94,0.4)';

    elEv.style.background = passed ? 'rgba(16,185,129,0.15)' : 'rgba(244,63,94,0.15)';

  }



  const elKelly = document.getElementById('quantKellyBadge');

  if (elKelly) {

    const lev = call.dynamic_leverage || 5;

    const risk = call.kelly_risk_pct || 1.5;

    elKelly.textContent = `Kelly: ${lev}x Lev • ${risk}% Risk`;

  }



  // 2. Hero Signal Card

  const elHero = document.getElementById('lsActionHero');

  const elHeroCard = document.getElementById('lsHeroCard');

  const elHeroSub = document.getElementById('lsActionSub');

  const elRegBadge = document.getElementById('lsSignalRegime');



  if (elHero) {

    if (call.signal_type === 'LONG') {

      elHero.innerHTML = '<span style="color:var(--emerald);">▲ LONG CALL (BUY SETUP)</span>';

      if (elHeroCard) elHeroCard.style.borderColor = 'rgba(16, 185, 129, 0.4)';

      if (elHeroSub) elHeroSub.textContent = `Bullish velocity +${call.velocity_15s.toFixed(1)} $/s. Target TP1 at $${(call.take_profit_1 || 0).toLocaleString()}.`;

    } else if (call.signal_type === 'SHORT') {

      elHero.innerHTML = '<span style="color:var(--rose);">▼ SHORT CALL (SELL SETUP)</span>';

      if (elHeroCard) elHeroCard.style.borderColor = 'rgba(244, 63, 94, 0.4)';

      if (elHeroSub) elHeroSub.textContent = `Bearish velocity ${call.velocity_15s.toFixed(1)} $/s. Target TP1 at $${(call.take_profit_1 || 0).toLocaleString()}.`;

    } else {

      elHero.innerHTML = '<span style="color:var(--text-muted);">⏸ NEUTRAL / STANDBY</span>';

      if (elHeroCard) elHeroCard.style.borderColor = 'rgba(255, 255, 255, 0.08)';

      if (elHeroSub) elHeroSub.textContent = 'Market in consolidation or choppy noise; standing aside to protect capital.';

    }

  }



  if (elRegBadge) elRegBadge.textContent = (call.regime || 'Scanning').replace('_', ' ');



  const elScore = document.getElementById('lsConvictionScore');

  if (elScore) elScore.textContent = (call.conviction_score || 3.0).toFixed(1);



  const confPct = Math.round((call.confidence || 0.5) * 100);

  const elConfBar = document.getElementById('lsConfidenceBar');

  if (elConfBar) elConfBar.style.width = `${confPct}%`;



  const elConfText = document.getElementById('lsConfidenceText');

  if (elConfText) elConfText.textContent = `Confidence: ${confPct}%`;



  const elRr = document.getElementById('lsRrRatio');

  if (elRr) elRr.textContent = call.risk_reward_ratio || '1 : 2.5';



  const elThesis = document.getElementById('lsThesisText');

  if (elThesis) elThesis.textContent = call.status_description || 'Analyzing market structure...';



  // 3. Dynamic Price Targets Ladder

  const elSl = document.getElementById('lsStopLossPrice');

  const elSlDist = document.getElementById('lsSlDistance');

  if (elSl) elSl.textContent = call.stop_loss ? `$${call.stop_loss.toLocaleString(undefined, {minimumFractionDigits: 1, maximumFractionDigits: 1})}` : '$0.00';

  if (elSlDist) elSlDist.textContent = `-$${(call.sl_distance_usd || 0).toFixed(1)}`;



  const elEntry = document.getElementById('lsEntryZonePrice');

  if (elEntry && call.entry_zone) {

    elEntry.textContent = `$${call.entry_zone[0].toLocaleString(undefined, {minimumFractionDigits: 1})} - $${call.entry_zone[1].toLocaleString(undefined, {minimumFractionDigits: 1})}`;

  }



  const elTp1 = document.getElementById('lsTp1Price');

  const elTp1Dist = document.getElementById('lsTp1Distance');

  if (elTp1) elTp1.textContent = call.take_profit_1 ? `$${call.take_profit_1.toLocaleString(undefined, {minimumFractionDigits: 1, maximumFractionDigits: 1})}` : '$0.00';

  if (elTp1Dist) elTp1Dist.textContent = `+$${(call.tp1_distance_usd || 0).toFixed(1)}`;



  const elTp2 = document.getElementById('lsTp2Price');

  const elTp2Dist = document.getElementById('lsTp2Distance');

  if (elTp2) elTp2.textContent = call.take_profit_2 ? `$${call.take_profit_2.toLocaleString(undefined, {minimumFractionDigits: 1, maximumFractionDigits: 1})}` : '$0.00';

  if (elTp2Dist) elTp2Dist.textContent = `+$${((call.tp1_distance_usd || 0) * 2.0).toFixed(1)}`;



  // 4. Update Calculator

  recalculatePositionSize();



  // 5. Append to Ledger Stream

  if (call.timestamp_utc && call.timestamp_utc !== lastLongShortTimestamp) {

    lastLongShortTimestamp = call.timestamp_utc;

    appendLongShortRow(call);

  }

}



function appendLongShortRow(call) {

  const tbody = document.getElementById('lsSignalFeedList');

  if (!tbody) return;



  const ph = tbody.querySelector('.feed-placeholder-row');

  if (ph) ph.remove();



  let callPill = '<span class="pill-call-neutral">NEUTRAL</span>';

  if (call.signal_type === 'LONG') {

    callPill = '<span class="pill-call-long">▲ LONG</span>';

  } else if (call.signal_type === 'SHORT') {

    callPill = '<span class="pill-call-short">▼ SHORT</span>';

  }



  const row = document.createElement('tr');

  row.innerHTML = `

    <td>${call.timestamp_utc}</td>

    <td>${callPill}</td>

    <td style="font-weight:700;">$${call.btc_spot.toLocaleString(undefined, {minimumFractionDigits: 1, maximumFractionDigits: 1})}</td>

    <td style="color:var(--cyan);">$${call.entry_zone[0].toLocaleString(undefined, {minimumFractionDigits: 1})} - $${call.entry_zone[1].toLocaleString(undefined, {minimumFractionDigits: 1})}</td>

    <td style="color:var(--rose);">$${call.stop_loss.toLocaleString(undefined, {minimumFractionDigits: 1, maximumFractionDigits: 1})}</td>

    <td style="color:var(--emerald);">$${call.take_profit_1.toLocaleString(undefined, {minimumFractionDigits: 1, maximumFractionDigits: 1})}</td>

    <td style="color:#34d399;">$${call.take_profit_2.toLocaleString(undefined, {minimumFractionDigits: 1, maximumFractionDigits: 1})}</td>

    <td style="font-weight:700; color:var(--cyan);">${call.risk_reward_ratio}</td>

    <td>${call.conviction_score.toFixed(1)} / 5.0</td>

    <td style="max-width:280px; white-space:nowrap; overflow:hidden; text-overflow:ellipsis;" title="${call.status_description}">${call.status_description}</td>

  `;



  tbody.insertBefore(row, tbody.firstChild);

  while (tbody.children.length > 25) {

    tbody.removeChild(tbody.lastChild);

  }

}



// Calculator event listeners

document.addEventListener('DOMContentLoaded', () => {

  const balInput = document.getElementById('calcBalanceInput');

  if (balInput) {

    balInput.addEventListener('input', recalculatePositionSize);

  }

});





/* ================= DIRECTIONAL PERP EXECUTION & POSITION CONTROLLER ================= */



let currentLeverage = 10;

let currentRiskPct = 2.0;

let activeDirectionalPosition = null;

let currentDirectionalStats = null;

let activeLedgerTab = 'open_positions';



async function fetchDirectionalPosition() {

  try {

    const res = await fetch('/api/jev/directional/position');

    if (!res.ok) return;

    const data = await res.json();

    renderDirectionalPositionState(data);

  } catch (e) {

    // console.warn('Directional position fetch error:', e);

  }

}



function renderDirectionalPositionState(data) {

  currentDirectionalStats = data.stats || {};

  activeDirectionalPosition = data.active_position;



  // Daily Goal & Circuit Breaker Strip Update

  if (data.daily_goal) {

    const dg = data.daily_goal;

    const elPnlTxt = document.getElementById('dailyGoalPnlText');

    const elProg = document.getElementById('dailyGoalProgressBar');

    const elCb = document.getElementById('dailyCircuitBreakerStatus');

    const elStance = document.getElementById('dailyGoalStanceBadge');



    if (elPnlTxt) {

      const cur = dg.current_pnl || 0;

      const tgt = dg.target_usd || 110;

      const pct = dg.progress_pct || 0;

      elPnlTxt.textContent = `${cur >= 0 ? '+' : ''}$${cur.toFixed(2)} / $${tgt.toFixed(0)} (${pct.toFixed(1)}%)`;

      elPnlTxt.style.color = cur >= 0 ? 'var(--emerald)' : 'var(--rose)';

    }



    if (elProg) {

      const pct = Math.max(0, Math.min(100, dg.progress_pct || 0));

      elProg.style.width = `${pct}%`;

    }



    if (elCb) {

      if (dg.circuit_breaker_tripped) {

        elCb.innerHTML = '<span style="color:#f43f5e; font-weight:700;">🚨 CIRCUIT BREAKER TRIPPED (-$40 limit hit). Trading locked to preserve capital.</span>';

      } else {

        const room = 40.0 + (dg.current_pnl || 0);

        elCb.innerHTML = `🛡️ Circuit Breaker: Safe (Max Loss -$40, buffer: $${Math.max(0, room).toFixed(1)})`;

      }

    }



    if (elStance) {

      if (dg.goal_reached) {

        elStance.textContent = '🏆 Daily Goal Reached (Vault Mode)';

        elStance.style.background = 'rgba(16,185,129,0.2)';

        elStance.style.color = 'var(--emerald)';

        elStance.style.borderColor = 'rgba(16,185,129,0.5)';

      } else if (dg.current_pnl < -20) {

        elStance.textContent = '🛡️ Capital Defense Mode';

        elStance.style.background = 'rgba(244,63,94,0.2)';

        elStance.style.color = '#f43f5e';

      } else {

        elStance.textContent = '⚡ Asymmetric Hunter';

        elStance.style.background = 'rgba(99,102,241,0.2)';

        elStance.style.color = '#a5b4fc';

      }

    }

  }



  // 1. Balance & Summary Strip
  // Handled authoritatively by multi-asset quant engine in pollAutopilotStatus ($100k bankroll, total realized PnL, win rate).
  // Avoid overwriting with legacy single-asset state.



  // 2. Auto-trade switch & status

  const elSwitch = document.getElementById('lsAutoTradeSwitch');

  const elStatus = document.getElementById('lsAutoTradeStatus');

  if (data.bot_config) {

    if (elSwitch && elSwitch.checked !== data.bot_config.auto_trade) {

      elSwitch.checked = data.bot_config.auto_trade;

    }

    if (elStatus) {

      if (data.bot_config.auto_trade) {

        elStatus.innerHTML = `<span style="color:#10b981; font-weight:700;">AUTONOMOUS JEV ACTIVE</span> • Dynamic leverage & ATR stops on MTF consensus (≥${data.bot_config.min_confidence}%)`;

      } else {

        elStatus.textContent = 'Auto-trade standby (Jev autonomous authority on MTF breakouts)';

      }

    }

  }



  // 3. Active Position Deck

  const noPosBlock = document.getElementById('noPositionBlock');

  const openCard = document.getElementById('openPositionCard');

  const btnLong = document.getElementById('btnOpenLong');

  const btnShort = document.getElementById('btnOpenShort');



  if (activeDirectionalPosition) {

    if (noPosBlock) noPosBlock.style.display = 'none';

    if (openCard) openCard.style.display = 'block';



    const pos = activeDirectionalPosition;

    const isLong = pos.side === 'LONG';



    if (openCard) {

      openCard.style.borderColor = isLong ? 'rgba(16, 185, 129, 0.6)' : 'rgba(244, 63, 94, 0.6)';

      openCard.style.boxShadow = isLong ? '0 0 20px rgba(16, 185, 129, 0.15)' : '0 0 20px rgba(244, 63, 94, 0.15)';

    }



    const badgeSide = document.getElementById('posSideBadge');

    if (badgeSide) {

      badgeSide.textContent = `${pos.side} ${pos.leverage}x`;

      badgeSide.className = `badge-pos-side ${isLong ? '' : 'short'}`;

    }



    const elDur = document.getElementById('posDuration');

    if (elDur && pos.opened_ts) {

      const elapsed = Math.max(0, Math.floor((Date.now() / 1000) - pos.opened_ts));

      const m = Math.floor(elapsed / 60).toString().padStart(2, '0');

      const s = (elapsed % 60).toString().padStart(2, '0');

      elDur.textContent = `${m}:${s}`;

    }



    const elPnlVal = document.getElementById('posUnrealizedPnl');

    const elRoeVal = document.getElementById('posRoePct');

    const pnl = pos.unrealized_pnl || 0;

    const roe = pos.roe_pct || 0;

    if (elPnlVal) {

      elPnlVal.textContent = `${pnl >= 0 ? '+' : ''}$${pnl.toFixed(2)}`;

      elPnlVal.style.color = pnl >= 0 ? 'var(--emerald)' : 'var(--rose)';

    }

    if (elRoeVal) {

      elRoeVal.textContent = `(${roe >= 0 ? '+' : ''}${roe.toFixed(2)}% ROE)`;

      elRoeVal.style.color = roe >= 0 ? 'var(--emerald)' : 'var(--rose)';

    }



    setElText('posEntryPrice', `$${pos.entry_price.toLocaleString(undefined, {minimumFractionDigits: 1, maximumFractionDigits: 1})}`);

    setElText('posMarkPrice', `$${pos.current_price.toLocaleString(undefined, {minimumFractionDigits: 1, maximumFractionDigits: 1})}`);

    setElText('posSizeUsd', `$${pos.size_usd.toLocaleString(undefined, {minimumFractionDigits: 1})} (${pos.size_btc} BTC)`);

    setElText('posMargin', `$${pos.margin.toLocaleString(undefined, {minimumFractionDigits: 2})}`);

    setElText('posStopLoss', `$${pos.stop_loss.toLocaleString(undefined, {minimumFractionDigits: 1})}`);

    setElText('posTp1', `$${pos.tp1.toLocaleString(undefined, {minimumFractionDigits: 1})}`);

    setElText('posTp2', `$${pos.tp2.toLocaleString(undefined, {minimumFractionDigits: 1})}`);

    setElText('posLiqPrice', `$${pos.liquidation_price.toLocaleString(undefined, {minimumFractionDigits: 1})}`);



    // Fee breakdown display

    const grossPnl = pos.gross_unrealized_pnl !== undefined ? pos.gross_unrealized_pnl : (pos.unrealized_pnl || 0);

    const totalFees = pos.total_fees !== undefined ? pos.total_fees : (pos.size_usd * 0.0008);

    setElText('posGrossPnl', `${grossPnl >= 0 ? '+' : ''}$${grossPnl.toFixed(2)}`);

    setElText('posTotalFees', `-$${totalFees.toFixed(2)}`);

    // Lifecycle Badges (Breakeven, Partial Profit, Trailing Stop, Thesis Age)

    const elLifecycle = document.getElementById('posLifecycleBadges');

    if (elLifecycle) {

      let badgesHtml = '';

      if (pos.breakeven_protected) {

        badgesHtml += '<span class="badge font-mono" style="padding:2px 8px; font-size:10px; border-radius:4px; background:rgba(16,185,129,0.2); border:1px solid rgba(16,185,129,0.5); color:var(--emerald); font-weight:700;">🛡️ FEE BREAKEVEN PROTECTED</span>';

      }

      if (pos.tp1_hit) {

        const banked = pos.partial_profit_banked ? ` (+$${pos.partial_profit_banked.toFixed(2)})` : '';

        badgesHtml += `<span class="badge font-mono" style="padding:2px 8px; font-size:10px; border-radius:4px; background:rgba(245,158,11,0.2); border:1px solid rgba(245,158,11,0.5); color:var(--amber); font-weight:700;">⚡ 50% PROFIT BANKED${banked}</span>`;

      }

      if (pos.trailing_active) {

        badgesHtml += '<span class="badge font-mono" style="padding:2px 8px; font-size:10px; border-radius:4px; background:rgba(99,102,241,0.2); border:1px solid rgba(99,102,241,0.5); color:var(--purple); font-weight:700;">📈 TRAILING STOP ACTIVE</span>';

      }

      if (pos.opened_ts) {

        const elapsedMin = Math.floor(Math.max(0, (Date.now() / 1000) - pos.opened_ts) / 60);

        badgesHtml += `<span class="badge font-mono" style="padding:2px 8px; font-size:10px; border-radius:4px; background:rgba(30,41,59,0.7); border:1px solid rgba(100,116,139,0.3); color:var(--text-muted);">⏳ Thesis Age: ${elapsedMin}m / 90m</span>`;

      }

      elLifecycle.innerHTML = badgesHtml;

    }



    const feeBadge = document.getElementById('posFeeBadge');

    if (feeBadge) feeBadge.textContent = `-$${totalFees.toFixed(2)} Fees`;



    // Redraw candlestick chart with trade overlays

    if (typeof renderCandleChart === 'function') renderCandleChart();



    if (btnLong) { btnLong.disabled = true; btnLong.style.opacity = '0.4'; }

    if (btnShort) { btnShort.disabled = true; btnShort.style.opacity = '0.4'; }

  } else {

    if (noPosBlock) noPosBlock.style.display = 'flex';

    if (openCard) openCard.style.display = 'none';

    if (btnLong) { btnLong.disabled = false; btnLong.style.opacity = '1'; }

    if (btnShort) { btnShort.disabled = false; btnShort.style.opacity = '1'; }

  }



  // Closed trades table and count are rendered authoritatively by pollAutopilotStatus
  // from /api/autotrade/status (reflecting all closed multi-asset positions).
}



function setElText(id, text) {

  const el = document.getElementById(id);

  if (el) el.textContent = text;

}



function renderClosedTradesTable(trades) {
  // Deprecated legacy handler: lsClosedTradesList & lsClosedTradeCount are managed by pollAutopilotStatus.
  return;



  if (!trades || trades.length === 0) {

    tbody.innerHTML = `

      <tr class="feed-placeholder-row">

        <td colspan="10">No closed directional trades yet. Click Open LONG or Open SHORT to execute your first trade!</td>

      </tr>`;

    return;

  }



  tbody.innerHTML = trades.map(t => {

    const isWin = (t.realized_pnl || 0) >= 0;

    const pnlColor = isWin ? 'var(--emerald)' : 'var(--rose)';

    const sidePill = t.side === 'LONG'

      ? `<span class="badge-binary" style="background:rgba(16,185,129,0.15); color:var(--emerald); border-color:rgba(16,185,129,0.3); font-size:11px;">▲ LONG</span>`

      : `<span class="badge-binary" style="background:rgba(244,63,94,0.15); color:var(--rose); border-color:rgba(244,63,94,0.3); font-size:11px;">▼ SHORT</span>`;



    const m = Math.floor((t.duration_sec || 0) / 60);

    const s = (t.duration_sec || 0) % 60;

    const durStr = `${m}m ${s}s`;



    let reasonBadge = `<span style="font-size:11px; padding:2px 6px; border-radius:4px; background:rgba(255,255,255,0.08); color:#e2e8f0;">${t.exit_reason || 'CLOSED'}</span>`;

    if (t.exit_reason === 'TAKE_PROFIT_2' || t.exit_reason === 'TAKE_PROFIT_1') {

      reasonBadge = `<span style="font-size:11px; padding:2px 6px; border-radius:4px; background:rgba(16,185,129,0.2); color:#10b981; border:1px solid rgba(16,185,129,0.3);">🎯 ${t.exit_reason.replace('_', ' ')}</span>`;

    } else if (t.exit_reason === 'STOP_LOSS') {

      reasonBadge = `<span style="font-size:11px; padding:2px 6px; border-radius:4px; background:rgba(244,63,94,0.2); color:#f43f5e; border:1px solid rgba(244,63,94,0.3);">🛑 STOP LOSS</span>`;

    }



    const fees = t.fees_paid !== undefined ? t.fees_paid : ((t.margin || 0) * 0.08);

    return `

      <tr>

        <td class="font-mono timestamp-cell" style="white-space:nowrap;">${t.closed_at || '--:--:--'}</td>

        <td>${sidePill}</td>

        <td class="font-mono">$${(t.entry_price || 0).toLocaleString(undefined, {minimumFractionDigits: 1})}</td>

        <td class="font-mono" style="font-weight:700;">$${(t.exit_price || 0).toLocaleString(undefined, {minimumFractionDigits: 1})}</td>

        <td class="font-mono">${t.leverage || 20}x</td>

        <td class="font-mono">$${(t.margin || 0).toFixed(2)}</td>

        <td class="font-mono text-amber" style="font-weight:600;">-$${fees.toFixed(2)}</td>

        <td class="font-mono" style="font-weight:800; color:${pnlColor};">${isWin ? '+' : ''}$${(t.realized_pnl || 0).toFixed(2)}</td>

        <td class="font-mono" style="font-weight:700; color:${pnlColor};">${isWin ? '+' : ''}${(t.roe_pct || 0).toFixed(2)}%</td>

        <td class="font-mono text-muted">${durStr}</td>

        <td>${reasonBadge}</td>

      </tr>

    `;

  }).join('');

}



async function openDirectionalTrade(side) {

  if (activeDirectionalPosition) {

    alert('A position is already open! Please close it first or let TP/SL execute.');

    return;

  }



  const balInput = document.getElementById('calcBalanceInput');

  const bal = balInput ? parseFloat(balInput.value) || 1000 : 1000;



  const payload = {

    side: side,

    leverage: (typeof calcLeverage !== 'undefined' ? calcLeverage : (currentLeverage || 10)),

    risk_pct: (typeof calcRiskPct !== 'undefined' ? calcRiskPct : (currentRiskPct || 2.0)),

    balance: bal,

  };



  if (currentLongShortData) {

    if (currentLongShortData.stop_loss) payload.stop_loss = currentLongShortData.stop_loss;

    if (currentLongShortData.take_profit_1) payload.tp1 = currentLongShortData.take_profit_1;

    if (currentLongShortData.take_profit_2) payload.tp2 = currentLongShortData.take_profit_2;

    if (currentLongShortData.status_description) payload.rationale = currentLongShortData.status_description;

  }



  try {

    const res = await fetch('/api/jev/directional/open', {

      method: 'POST',

      headers: { 'Content-Type': 'application/json' },

      body: JSON.stringify(payload)

    });

    const data = await res.json();

    if (data.success) {

      await fetchDirectionalPosition();

    } else {

      alert(data.error || 'Failed to open position.');

    }

  } catch (e) {

    alert(`Execution error: ${e.message}`);

  }

}



async function closeDirectionalTrade() {

  if (!activeDirectionalPosition) return;

  try {

    const res = await fetch('/api/jev/directional/close', {

      method: 'POST',

      headers: { 'Content-Type': 'application/json' },

      body: JSON.stringify({ reason: 'USER_MARKET_EXIT' })

    });

    const data = await res.json();

    if (data.success) {

      await fetchDirectionalPosition();

    } else {

      alert(data.error || 'Failed to close position.');

    }

  } catch (e) {

    alert(`Close error: ${e.message}`);

  }

}



async function toggleDirectionalAutoTrade(enabled) {

  try {

    const res = await fetch('/api/jev/directional/config', {

      method: 'POST',

      headers: { 'Content-Type': 'application/json' },

      body: JSON.stringify({

        auto_trade: enabled,

        leverage: (typeof calcLeverage !== 'undefined' ? calcLeverage : (currentLeverage || 10)),

        risk_pct: currentRiskPct || 2.0

      })

    });

    const data = await res.json();

    if (data.success) {

      await fetchDirectionalPosition();

    }

  } catch (e) {

    console.error('Config error:', e);

  }

}



async function resetDirectionalAccount() {

  if (!confirm('Reset paper trading balance to $1,000 and clear directional trade history?')) {

    return;

  }

  try {

    const res = await fetch('/api/jev/directional/reset', { method: 'POST' });

    const data = await res.json();

    if (data.success) {

      await fetchDirectionalPosition();

    }

  } catch (e) {

    console.error('Reset error:', e);

  }

}



function switchLongShortLedgerTab(tab) {
  activeLedgerTab = tab;
  const btnOpenPos = document.getElementById('btnLedgerOpenPositions');
  const btnSignals = document.getElementById('btnLedgerSignals');
  const btnTrades = document.getElementById('btnLedgerTrades');

  const wrapOpenPos = document.getElementById('lsOpenPositionsWrap');
  const wrapSignals = document.getElementById('lsSignalStreamWrap');
  const wrapTrades = document.getElementById('lsClosedTradesWrap');

  const statusText = document.getElementById('lsLedgerStatusText');

  if (btnOpenPos) btnOpenPos.classList.remove('active');
  if (btnSignals) btnSignals.classList.remove('active');
  if (btnTrades) btnTrades.classList.remove('active');

  if (wrapOpenPos) wrapOpenPos.style.display = 'none';
  if (wrapSignals) wrapSignals.style.display = 'none';
  if (wrapTrades) wrapTrades.style.display = 'none';

  if (tab === 'open_positions') {
    if (btnOpenPos) btnOpenPos.classList.add('active');
    if (wrapOpenPos) wrapOpenPos.style.display = 'block';
    if (statusText) statusText.textContent = 'Live Open Positions Desk (Real-Time Mark & PnL)';
  } else if (tab === 'signals') {
    if (btnSignals) btnSignals.classList.add('active');
    if (wrapSignals) wrapSignals.style.display = 'block';
    if (statusText) statusText.textContent = 'Streaming signals every ~1.5s';
  } else {
    if (btnTrades) btnTrades.classList.add('active');
    if (wrapTrades) wrapTrades.style.display = 'block';
    if (statusText) statusText.textContent = 'Tick-by-tick realized PnL audit log';
  }
}



function clearActiveLedgerFeed() {

  if (activeLedgerTab === 'signals') {

    const list = document.getElementById('lsSignalFeedList');

    if (list) list.innerHTML = '';

  } else {

    resetDirectionalAccount();

  }

}





/* ================= LIVE CANDLESTICK CHART & DYNAMIC TRADE OVERLAYS ================= */



let currentCandleSymbol = 'BTCUSDT';
let currentCandleInterval = '5m';
let currentCandles = [];
let candlePollInterval = null;

async function fetchCandleData() {
  try {
    const res = await fetch(`/api/jev/klines?symbol=${encodeURIComponent(currentCandleSymbol)}&interval=${currentCandleInterval}&limit=42`);
    if (!res.ok) return;
    const data = await res.json();
    if (data && data.candles) {
      currentCandles = data.candles;
      renderCandleChart();
      updateCandleOhlcBar(currentCandles[currentCandles.length - 1]);
    }
  } catch (e) {
    // console.warn('Candle fetch error:', e);
  }
}

function setChartSymbol(sym) {
  if (!sym) return;
  currentCandleSymbol = sym.toUpperCase().trim();
  
  // Update UI quick pills
  const pills = document.querySelectorAll('.chart-asset-pill');
  pills.forEach(p => {
    p.classList.toggle('active', p.getAttribute('data-sym') === currentCandleSymbol);
  });
  
  // Update select dropdown if present
  const selectEl = document.getElementById('chartAssetSelect');
  if (selectEl && selectEl.value !== currentCandleSymbol) {
    selectEl.value = currentCandleSymbol;
  }
  
  // Update title
  const titleEl = document.getElementById('activeChartTitle');
  if (titleEl) {
    let displaySym = currentCandleSymbol;
    if (displaySym === 'XAUUSDT') displaySym = 'GOLD (XAU/USD)';
    else if (displaySym === 'PAXGUSDT') displaySym = 'PAXOS GOLD (Spot)';
    titleEl.textContent = `${displaySym} Real-Time Candlestick Chart`;
  }
  
  fetchCandleData();
}

function setCandleInterval(interval) {
  currentCandleInterval = interval;
  ['btnCandle1m', 'btnCandle5m', 'btnCandle15m'].forEach(id => {
    const btn = document.getElementById(id);
    if (btn) btn.classList.toggle('active', id === `btnCandle${interval}`);
  });
  fetchCandleData();
}



function updateCandleOhlcBar(candle) {
  if (!candle) return;
  const dec = candle.close >= 100 ? 1 : (candle.close >= 1 ? 2 : 4);
  const setV = (id, v) => {
    const el = document.getElementById(id);
    if (el) el.textContent = `$${v.toLocaleString(undefined, {minimumFractionDigits: dec, maximumFractionDigits: dec})}`;
  };

  setV('ohlcOpen', candle.open);

  setV('ohlcHigh', candle.high);

  setV('ohlcLow', candle.low);

  setV('ohlcClose', candle.close);



  const cClose = document.getElementById('ohlcClose');

  if (cClose) {

    cClose.style.color = candle.close >= candle.open ? 'var(--emerald)' : 'var(--rose)';

  }

}



function renderCandleChart() {

  const canvas = document.getElementById('lsCandleCanvas');

  if (!canvas || !currentCandles || currentCandles.length === 0) return;



  const rect = canvas.getBoundingClientRect();

  const dpr = window.devicePixelRatio || 1;

  const w = rect.width;

  const h = rect.height;



  if (canvas.width !== Math.floor(w * dpr) || canvas.height !== Math.floor(h * dpr)) {

    canvas.width = Math.floor(w * dpr);

    canvas.height = Math.floor(h * dpr);

  }



  const ctx = canvas.getContext('2d');

  ctx.save();

  ctx.scale(dpr, dpr);



  // Clear Background

  ctx.fillStyle = '#070b14';

  ctx.fillRect(0, 0, w, h);



  // Layout Boundaries

  const padRight = 68; // Price axis on right

  const padBottom = 22; // Time axis on bottom

  const chartW = w - padRight;

  const chartH = h - padBottom;

  const volumeH = chartH * 0.18;

  const priceH = chartH - volumeH;



  // 1. Calculate Price Scale (Min & Max)

  let minP = Infinity;

  let maxP = -Infinity;

  let maxVol = 0;



  currentCandles.forEach(c => {

    if (c.low < minP) minP = c.low;

    if (c.high > maxP) maxP = c.high;

    if (c.volume > maxVol) maxVol = c.volume;

  });



  // Include Active Position or Signal levels in range so lines are never off-screen

  const targetLevels = [];

  if (activeDirectionalPosition) {

    const pos = activeDirectionalPosition;

    targetLevels.push({ price: pos.entry_price, label: 'ENTRY', color: '#06b6d4', dash: [4, 4] });

    targetLevels.push({ price: pos.stop_loss, label: 'SL', color: '#f43f5e', dash: [3, 3] });

    targetLevels.push({ price: pos.tp1, label: 'TP1', color: '#10b981', dash: [4, 4] });

    targetLevels.push({ price: pos.tp2, label: 'TP2', color: '#34d399', dash: [4, 4] });

  } else if (currentLongShortData && currentLongShortData.signal_type !== 'NEUTRAL') {

    if (currentLongShortData.stop_loss) targetLevels.push({ price: currentLongShortData.stop_loss, label: 'SL (Jev)', color: 'rgba(244, 63, 94, 0.7)', dash: [2, 3] });

    if (currentLongShortData.take_profit_1) targetLevels.push({ price: currentLongShortData.take_profit_1, label: 'TP1 (Jev)', color: 'rgba(16, 185, 129, 0.7)', dash: [2, 3] });

  }



  targetLevels.forEach(t => {

    if (t.price < minP) minP = t.price;

    if (t.price > maxP) maxP = t.price;

  });



  // Add 6% top & bottom padding

  const pRange = (maxP - minP) || 100;

  minP -= pRange * 0.06;

  maxP += pRange * 0.06;

  const adjRange = maxP - minP;



  const getY = (p) => priceH - ((p - minP) / adjRange) * priceH;



  // 2. Draw Horizontal Price Grid Lines

  ctx.lineWidth = 1;

  ctx.strokeStyle = 'rgba(255, 255, 255, 0.04)';

  ctx.fillStyle = 'rgba(255, 255, 255, 0.35)';

  ctx.font = '10px JetBrains Mono, monospace';

  ctx.textAlign = 'left';



  const gridSteps = 5;

  for (let i = 0; i <= gridSteps; i++) {

    const gridP = minP + (adjRange * (i / gridSteps));

    const y = getY(gridP);

    ctx.beginPath();

    ctx.moveTo(0, y);

    ctx.lineTo(chartW, y);

    ctx.stroke();



    // Right Axis Price Label

    const axisDec = adjRange < 1 ? 4 : (adjRange < 15 ? 2 : 1);
    ctx.fillText(`$${gridP.toFixed(axisDec)}`, chartW + 8, y + 3);

  }



  // 3. Draw Volume Histogram Bars

  const n = currentCandles.length;

  const candleSlotW = chartW / n;

  const candleW = Math.max(3, candleSlotW * 0.65);



  currentCandles.forEach((c, idx) => {

    const x = idx * candleSlotW + candleSlotW / 2;

    const isBull = c.close >= c.open;

    const vNorm = maxVol > 0 ? (c.volume / maxVol) : 0;

    const vH = vNorm * volumeH;

    const vY = chartH - vH;



    ctx.fillStyle = isBull ? 'rgba(16, 185, 129, 0.18)' : 'rgba(244, 63, 94, 0.18)';

    ctx.fillRect(x - candleW / 2, vY, candleW, vH);

  });



  // 4. Draw Candlesticks (Wick + Body)

  currentCandles.forEach((c, idx) => {

    const x = idx * candleSlotW + candleSlotW / 2;

    const isBull = c.close >= c.open;

    const color = isBull ? '#10b981' : '#f43f5e';



    const yHigh = getY(c.high);

    const yLow = getY(c.low);

    const yOpen = getY(c.open);

    const yClose = getY(c.close);



    // Wick

    ctx.strokeStyle = color;

    ctx.lineWidth = 1.2;

    ctx.beginPath();

    ctx.moveTo(x, yHigh);

    ctx.lineTo(x, yLow);

    ctx.stroke();



    // Body

    const bodyTop = Math.min(yOpen, yClose);

    const bodyH = Math.max(1.8, Math.abs(yClose - yOpen));

    ctx.fillStyle = color;

    ctx.fillRect(x - candleW / 2, bodyTop, candleW, bodyH);

  });



  // 5. Draw Dynamic Target Overlay Lines (Entry, SL, TP1, TP2)

  targetLevels.forEach(t => {

    const y = getY(t.price);

    if (y < 0 || y > chartH) return;



    ctx.save();

    ctx.strokeStyle = t.color;

    ctx.lineWidth = 1.5;

    ctx.setLineDash(t.dash || [4, 4]);



    ctx.beginPath();

    ctx.moveTo(0, y);

    ctx.lineTo(chartW, y);

    ctx.stroke();



    // Right-side badge

    ctx.fillStyle = t.color;

    ctx.fillRect(chartW + 2, y - 9, padRight - 4, 18);

    ctx.fillStyle = '#000';

    ctx.font = 'bold 9px JetBrains Mono, monospace';

    ctx.textAlign = 'center';

    ctx.fillText(t.label, chartW + (padRight / 2), y + 3);

    ctx.restore();

  });



  // 6. Draw Current Spot Price Line & Pulse Dot

  const lastCandle = currentCandles[currentCandles.length - 1];

  if (lastCandle) {

    const curP = (currentLongShortData && currentLongShortData.btc_spot) || lastCandle.close;

    const curY = getY(curP);



    ctx.save();

    ctx.strokeStyle = '#06b6d4';

    ctx.lineWidth = 1;

    ctx.setLineDash([2, 2]);

    ctx.beginPath();

    ctx.moveTo(0, curY);

    ctx.lineTo(chartW, curY);

    ctx.stroke();



    // Pulsing dot on the latest candle

    const lastX = (n - 1) * candleSlotW + candleSlotW / 2;

    ctx.fillStyle = '#06b6d4';

    ctx.beginPath();

    ctx.arc(lastX, curY, 3.5, 0, Math.PI * 2);

    ctx.fill();



    // Current price tag on right axis

    ctx.fillStyle = '#06b6d4';

    ctx.fillRect(chartW + 2, curY - 9, padRight - 4, 18);

    ctx.fillStyle = '#000';

    ctx.font = 'bold 10px JetBrains Mono, monospace';

    ctx.textAlign = 'center';

    ctx.fillText(`$${curP.toFixed(1)}`, chartW + (padRight / 2), curY + 3.5);

    ctx.restore();

  }



  // 7. Update Top Chart Legend Overlay Badges

  updateChartLegend();



  ctx.restore();

}



function updateChartLegend() {

  const setLeg = (id, valId, val, show) => {

    const el = document.getElementById(id);

    const vEl = document.getElementById(valId);

    if (!el) return;

    if (show && val) {

      el.style.display = 'inline-flex';

      if (vEl) vEl.textContent = `$${val.toLocaleString(undefined, {minimumFractionDigits: 1, maximumFractionDigits: 1})}`;

    } else {

      el.style.display = 'none';

    }

  };



  if (activeDirectionalPosition) {

    const pos = activeDirectionalPosition;

    setLeg('legendEntry', 'legEntryVal', pos.entry_price, true);

    setLeg('legendSl', 'legSlVal', pos.stop_loss, true);

    setLeg('legendTp1', 'legTp1Val', pos.tp1, true);

    setLeg('legendTp2', 'legTp2Val', pos.tp2, true);

  } else if (currentLongShortData && currentLongShortData.signal_type !== 'NEUTRAL') {

    setLeg('legendEntry', 'legEntryVal', currentLongShortData.entry_zone ? currentLongShortData.entry_zone[0] : null, true);

    setLeg('legendSl', 'legSlVal', currentLongShortData.stop_loss, true);

    setLeg('legendTp1', 'legTp1Val', currentLongShortData.take_profit_1, true);

    setLeg('legendTp2', 'legTp2Val', currentLongShortData.take_profit_2, true);

  } else {

    setLeg('legendEntry', 'legEntryVal', null, false);

    setLeg('legendSl', 'legSlVal', null, false);

    setLeg('legendTp1', 'legTp1Val', null, false);

    setLeg('legendTp2', 'legTp2Val', null, false);

  }

}



window.setCandleInterval = setCandleInterval;
window.setChartSymbol = setChartSymbol;

window.renderCandleChart = renderCandleChart;

window.fetchCandleData = fetchCandleData;

window.openDirectionalTrade = openDirectionalTrade;

window.closeDirectionalTrade = closeDirectionalTrade;

window.toggleDirectionalAutoTrade = toggleDirectionalAutoTrade;

window.resetDirectionalAccount = resetDirectionalAccount;

window.switchLongShortLedgerTab = switchLongShortLedgerTab;



// --- MULTI-ASSET SCANNER & LIQUIDATION RADAR UPDATE ---

async function pollMultiAssetScanner() {

  try {

    const res = await fetch('/api/multi-asset/scan');

    const data = await res.json();

    if (data && data.assets) {

      const btc = data.assets['BTCUSDT'];

      const eth = data.assets['ETHUSDT'];

      const sol = data.assets['SOLUSDT'];
      // Feed Global Header Live Tickers
      if (btc) {
        const hp = document.getElementById('hdrPriceBtc');
        const hc = document.getElementById('hdrChgBtc');
        if (hp) hp.textContent = `$${btc.price.toLocaleString(undefined, {minimumFractionDigits: 1, maximumFractionDigits: 1})}`;
        if (hc) {
          hc.textContent = `${btc.change_24h_pct >= 0 ? '+' : ''}${btc.change_24h_pct}%`;
          hc.style.color = btc.change_24h_pct >= 0 ? 'var(--emerald)' : 'var(--rose)';
        }
      }
      if (eth) {
        const hp = document.getElementById('hdrPriceEth');
        const hc = document.getElementById('hdrChgEth');
        if (hp) hp.textContent = `$${eth.price.toLocaleString(undefined, {minimumFractionDigits: 2, maximumFractionDigits: 2})}`;
        if (hc) {
          hc.textContent = `${eth.change_24h_pct >= 0 ? '+' : ''}${eth.change_24h_pct}%`;
          hc.style.color = eth.change_24h_pct >= 0 ? 'var(--emerald)' : 'var(--rose)';
        }
      }
      if (sol) {
        const hp = document.getElementById('hdrPriceSol');
        const hc = document.getElementById('hdrChgSol');
        if (hp) hp.textContent = `$${sol.price.toLocaleString(undefined, {minimumFractionDigits: 2, maximumFractionDigits: 2})}`;
        if (hc) {
          hc.textContent = `${sol.change_24h_pct >= 0 ? '+' : ''}${sol.change_24h_pct}%`;
          hc.style.color = sol.change_24h_pct >= 0 ? 'var(--emerald)' : 'var(--rose)';
        }
      }




      const elBtc = document.getElementById('scannerBtcBadge');

      if (elBtc && btc) {

        elBtc.textContent = `BTC: $${btc.price.toLocaleString()} (${btc.change_24h_pct >= 0 ? '+' : ''}${btc.change_24h_pct}%) • ${btc.opportunity_score} Score`;

      }

      const elEth = document.getElementById('scannerEthBadge');

      if (elEth && eth) {

        elEth.textContent = `ETH: $${eth.price.toLocaleString()} (${eth.change_24h_pct >= 0 ? '+' : ''}${eth.change_24h_pct}%) • ${eth.opportunity_score} Score`;

      }

      const elSol = document.getElementById('scannerSolBadge');

      if (elSol && sol) {

        elSol.textContent = `SOL: $${sol.price.toLocaleString()} (${sol.change_24h_pct >= 0 ? '+' : ''}${sol.change_24h_pct}%) • ${sol.opportunity_score} Score`;

      }

    }

  } catch (e) {}

}



async function runMonteCarloSimulation() {

  const btn = document.getElementById('btnRunMonteCarlo');

  if (btn) btn.textContent = 'Running 10k Sims...';

  try {

    const res = await fetch('/api/sim/monte-carlo');

    const data = await res.json();

    if (btn) btn.textContent = '🎲 10k Monte Carlo Sim';

    alert(

      `🎲 10,000 MONTE CARLO SIMULATION RESULTS (30-Day Horizon):\n\n` +

      `• Probability of Hitting €100/Day: ${data.probability_daily_goal_pct}%\n` +

      `• Median 30-Day Balance: $${data.median_30d_balance.toLocaleString()} (+$${data.expected_monthly_profit_usd.toLocaleString()} Profit)\n` +

      `• Conservative (P10) Balance: $${data.p10_conservative_balance.toLocaleString()}\n` +

      `• Exceptional (P90) Balance: $${data.p90_exceptional_balance.toLocaleString()}\n` +

      `• 95th Percentile Max Drawdown: ${data.worst_case_drawdown_p95_pct}%\n\n` +

      `Circuit Breaker: ${data.circuit_breaker_safety}`

    );

  } catch (e) {

    if (btn) btn.textContent = '🎲 10k Monte Carlo Sim';

    alert('Simulation completed.');

  }

}



async function testTelegramAlert() {

  const btn = document.getElementById('btnTestTelegram');

  if (btn) btn.textContent = 'Sending...';

  try {

    const res = await fetch('/api/telegram/test', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: '{}' });

    const data = await res.json();

    if (btn) btn.textContent = '📱 Telegram Alert';

    alert(`📱 Telegram Alert Triggered!\nMode: ${data.mode || 'SENT'}\nCheck your phone or runtime/telegram_outbox.json.`);

  } catch (e) {

    if (btn) btn.textContent = '📱 Telegram Alert';

  }

}



// Call multi-asset scanner every 8 seconds

setInterval(pollMultiAssetScanner, 8000);

pollMultiAssetScanner();





// ============================================================================

// BINANCE MARKET UNIVERSE & MULTI-ASSET DUAL-MODE EXECUTION

// ============================================================================

let currentBinanceMode = 'PAPER';



async function fetchBinanceUniverse(force = false) {

  try {

    const res = await fetch(`/api/binance/universe${force ? '?force=true' : ''}`);

    const data = await res.json();

    if (data.status === 'ok' && data.ranked_universe) {

      renderBinanceUniverse(data.ranked_universe);

    }

  } catch (err) {

    console.warn('Failed to fetch Binance universe:', err);

  }

}



async function fetchBinanceMode() {

  try {

    const res = await fetch('/api/binance/mode');

    const data = await res.json();

    currentBinanceMode = data.mode || 'PAPER';

    const badge = document.getElementById('binanceModeBadge');

    if (badge) {

      if (currentBinanceMode === 'LIVE') {

        badge.textContent = 'LIVE BINANCE';

        badge.style.background = 'rgba(239, 68, 68, 0.2)';

        badge.style.color = '#f87171';

        badge.style.border = '1px solid rgba(239, 68, 68, 0.5)';

      } else {

        badge.textContent = 'PAPER TRADING';

        badge.style.background = 'rgba(16, 185, 129, 0.2)';

        badge.style.color = '#34d399';

        badge.style.border = '1px solid rgba(16, 185, 129, 0.4)';

      }

    }

  } catch (e) {

    console.warn('fetchBinanceMode error:', e);

  }

}



async function toggleBinanceMode() {

  const target = currentBinanceMode === 'PAPER' ? 'live' : 'paper';

  if (target === 'live') {

    const proceed = confirm('⚠️ Switch to LIVE BINANCE Execution?\n\nThis will place real orders on your Binance account if API keys are configured in .env.');

    if (!proceed) return;

  }

  try {

    const res = await fetch('/api/binance/mode', {

      method: 'POST',

      headers: { 'Content-Type': 'application/json' },

      body: JSON.stringify({ mode: target })

    });

    const result = await res.json();

    if (result.success) {

      await fetchBinanceMode();

    } else {

      alert(result.error || 'Failed to switch mode. Please check .env credentials.');

    }

  } catch (err) {

    alert('Error switching mode: ' + err.message);

  }

}



function renderBinanceUniverse(pairs) {
  const container = document.getElementById('binanceUniverseTiles');
  if (!container) return;

  // Populate Chart Asset Dropdown with Universe Assets
  const assetSelect = document.getElementById('chartAssetSelect');
  if (assetSelect && pairs && pairs.length > 0) {
    const curVal = currentCandleSymbol;
    const existing = new Set(Array.from(assetSelect.options).map(o => o.value));
    pairs.forEach(p => {
      if (!existing.has(p.symbol)) {
        const opt = document.createElement('option');
        opt.value = p.symbol;
        opt.textContent = `${p.symbol} (${p.change_24h_pct >= 0 ? '+' : ''}${p.change_24h_pct}%)`;
        assetSelect.appendChild(opt);
        existing.add(p.symbol);
      }
    });
    assetSelect.value = curVal;
  }

  // Update Header Gold Ticker if present
  const goldPair = pairs.find(p => p.symbol === 'XAUUSDT' || p.symbol === 'PAXGUSDT');
  if (goldPair) {
    const gPx = document.getElementById('hdrPriceGold');
    const gChg = document.getElementById('hdrChgGold');
    if (gPx) gPx.textContent = `$${goldPair.price.toLocaleString(undefined, {minimumFractionDigits: 2, maximumFractionDigits: 2})}`;
    if (gChg) {
      gChg.textContent = `${goldPair.change_24h_pct >= 0 ? '+' : ''}${goldPair.change_24h_pct}%`;
      gChg.style.color = goldPair.change_24h_pct >= 0 ? 'var(--emerald)' : 'var(--rose)';
    }
  }



  if (!pairs || pairs.length === 0) {

    container.innerHTML = '<div style="color:var(--text-muted); font-size:12px; grid-column:1/-1; text-align:center;">No high-volume pairs available.</div>';

    return;

  }



  container.innerHTML = pairs.slice(0, 10).map((p, idx) => {

    const changeColor = p.change_24h_pct >= 0 ? 'var(--emerald)' : 'var(--rose)';

    const changeSign = p.change_24h_pct >= 0 ? '+' : '';

    const volStr = (p.volume_24h_usd / 1e6).toFixed(1) + 'M';

    const isTop = idx === 0;

    

    return `

      <div onclick="setChartSymbol('${p.symbol}')" title="Click to view chart for ${p.symbol}" style="cursor:pointer; background:rgba(30,41,59,0.7); border:1px solid ${isTop ? 'rgba(245,158,11,0.5)' : 'rgba(255,255,255,0.08)'}; border-radius:8px; padding:8px 10px; display:flex; flex-direction:column; justify-content:space-between; gap:6px;">

        <div style="display:flex; justify-content:space-between; align-items:center;">

          <span style="font-size:12px; font-weight:800; color:#fff;">

            ${isTop ? '👑 ' : ''}${p.symbol}

          </span>

          <span style="font-size:11px; font-weight:700; color:${changeColor};">

            ${changeSign}${p.change_24h_pct}%

          </span>

        </div>

        

        <div style="display:flex; justify-content:space-between; align-items:baseline; font-family:var(--font-mono);">

          <span style="font-size:13px; font-weight:700; color:#f1f5f9;">$${p.price.toLocaleString()}</span>

          <span style="font-size:10px; color:var(--text-muted);">$${volStr}</span>

        </div>



        <div style="display:flex; justify-content:space-between; align-items:center; gap:4px; font-size:10px;">

          <span class="badge" style="padding:1px 5px; font-size:9px; border-radius:4px; background:rgba(255,255,255,0.06); color:var(--text-muted); font-weight:600;">

            ${p.badge || 'CHOP'}

          </span>

          <span style="font-weight:700; color:${p.opportunity_score >= 70 ? 'var(--emerald)' : 'var(--amber)'};">

            ⚡ ${p.opportunity_score}

          </span>

          <button onclick="executeBinanceTrade('${p.symbol}', '${p.direction === 'SHORT' ? 'SELL' : 'BUY'}', ${p.price})" style="background:rgba(245,158,11,0.15); border:1px solid rgba(245,158,11,0.4); color:#fbbf24; font-size:10px; font-weight:700; padding:2px 6px; border-radius:4px; cursor:pointer;" title="Execute ${p.direction === 'SHORT' ? 'Short' : 'Long'} on ${p.symbol}">

            Trade

          </button>

        </div>

      </div>

    `;

  }).join('');

}



async function executeBinanceTrade(symbol, side, price) {

  const modeStr = currentBinanceMode === 'LIVE' ? '🔴 LIVE BINANCE' : '🟢 PAPER';

  const qty = symbol.startsWith('BTC') ? 0.01 : (symbol.startsWith('ETH') ? 0.1 : 2.0);

  

  const confirmed = confirm(`Execute ${side} order on ${symbol}?\n\nMode: ${modeStr}\nPrice: $${price}\nQuantity: ${qty}`);

  if (!confirmed) return;



  try {

    const res = await fetch('/api/binance/execute', {

      method: 'POST',

      headers: { 'Content-Type': 'application/json' },

      body: JSON.stringify({ symbol, side, price, quantity: qty })

    });

    const result = await res.json();

    if (result.success) {

      alert(`✅ Order Filled (${modeStr})!\n\nSymbol: ${symbol}\nSide: ${side}\nPrice: $${price}\nFee Rate: 0.015% Maker`);

      if (typeof fetchDirectionalPosition === 'function') fetchDirectionalPosition();

    } else {

      alert('❌ Execution error: ' + (result.error || 'Failed to place order'));

    }

  } catch (err) {

    alert('Network error during execution: ' + err.message);

  }

}



// Auto-initialize Binance universe polling

setInterval(fetchBinanceUniverse, 15000);

setTimeout(fetchBinanceUniverse, 1500);

setTimeout(fetchBinanceMode, 1000);





// ============================================================================

// 24/7 AUTOPILOT MULTI-ASSET TRADER CLIENT CONTROLLER

// ============================================================================

let isAutopilotRunning = false;



async function pollAutopilotStatus() {

  try {

    const res = await fetch('/api/autotrade/status');

    const data = await res.json();

    isAutopilotRunning = !!data.running;



    const btn = document.getElementById('btnToggleAutopilot');

    const pulse = document.getElementById('autopilotPulse');

    const statusText = document.getElementById('autopilotStatusText');

    const posCount = document.getElementById('autopilotPosCount');

    const dailyPnl = document.getElementById('autopilotDailyPnl');

    const posList = document.getElementById('autopilotPositionsList');

    // Synchronize Global Header Master Switch & Telemetry
    const hdrBtn = document.getElementById('btnHeaderAutopilotToggle');
    const hdrStatus = document.getElementById('hdrAutopilotStatus');
    const hdrDot = document.getElementById('hdrAutopilotDot');
    const hdrPnl = document.getElementById('hdrDailyPnl');

    if (hdrBtn) {
      hdrBtn.textContent = isAutopilotRunning ? '⏹ STOP AUTOPILOT' : '▶ START AUTOPILOT';
      hdrBtn.className = isAutopilotRunning ? 'btn-master-toggle running' : 'btn-master-toggle';
    }
    if (hdrStatus) {
      hdrStatus.textContent = isAutopilotRunning ? 'AUTOPILOT: HUNTING' : 'AUTOPILOT: STANDBY';
      hdrStatus.className = isAutopilotRunning ? 'autopilot-label font-mono active' : 'autopilot-label font-mono';
    }
    if (hdrDot) {
      hdrDot.className = isAutopilotRunning ? 'autopilot-dot active' : 'autopilot-dot';
    }
    if (hdrPnl) {
      const pnlVal = data.daily_pnl_usd || 0.0;
      hdrPnl.textContent = (pnlVal >= 0 ? '+$' : '-$') + Math.abs(pnlVal).toFixed(2);
      hdrPnl.style.color = pnlVal >= 0 ? 'var(--emerald)' : 'var(--rose)';
    }



    if (btn) {

      if (isAutopilotRunning) {

        btn.textContent = '⏹ STOP AUTOPILOT';

        btn.style.background = '#ef4444';

        btn.style.boxShadow = '0 4px 14px rgba(239, 68, 68, 0.4)';

      } else {

        btn.textContent = '▶ START AUTOPILOT';

        btn.style.background = '#4f46e5';

        btn.style.boxShadow = '0 4px 14px rgba(79, 70, 229, 0.4)';

      }

    }



    if (pulse && statusText) {

      if (isAutopilotRunning) {

        pulse.style.background = '#10b981';

        pulse.style.boxShadow = '0 0 10px #10b981';

        statusText.textContent = 'AUTOPILOT: ACTIVE (HUNTING BTC, ETH, SOL)';

        statusText.style.color = '#34d399';

      } else {

        pulse.style.background = '#94a3b8';

        pulse.style.boxShadow = 'none';

        statusText.textContent = 'AUTOPILOT: STOPPED';

        statusText.style.color = '#94a3b8';

      }

    }



    if (posCount) {

      posCount.textContent = `${data.open_positions_count || 0} / ${data.max_concurrent_positions || 15}`;

    }



    if (dailyPnl) {

      const pnlVal = data.daily_pnl_usd || 0.0;

      dailyPnl.textContent = (pnlVal >= 0 ? '+$' : '-$') + Math.abs(pnlVal).toFixed(2);

      dailyPnl.style.color = pnlVal >= 0 ? 'var(--emerald)' : 'var(--rose)';

    }



    if (posList) {

      const positions = data.open_positions || [];

      if (positions.length === 0) {

        posList.innerHTML = `<span style="font-size:12px; color:var(--text-muted); font-style:italic;">${isAutopilotRunning ? '⚡ Jev Brain is scanning 5s orderbooks on BTC, ETH, SOL for high-velocity setups...' : 'Autopilot stopped. Click Start Autopilot to begin.'}</span>`;

      } else {

        posList.innerHTML = positions.map(p => {

          const uPnlUsd = (typeof p.unrealized_pnl_usd === 'number') ? p.unrealized_pnl_usd : 0.0;

          const uPnlPct = (typeof p.unrealized_pnl_pct === 'number') ? p.unrealized_pnl_pct : 0.0;

          const isProfit = (uPnlUsd >= 0);

          const pnlColor = isProfit ? 'var(--emerald)' : 'var(--rose)';

          const pnlSign = isProfit ? '+' : '';

          const entryPx = (typeof p.entry_price === 'number') ? p.entry_price.toLocaleString() : '---';

          const curPx = (typeof p.current_price === 'number') ? p.current_price.toLocaleString() : '---';

          const isLong = (p.side === 'LONG');

          const sideColor = isLong ? '#34d399' : '#f87171';

          const sideBg = isLong ? 'rgba(16,185,129,0.15)' : 'rgba(239,68,68,0.15)';

          const sideBorder = isLong ? 'rgba(16,185,129,0.35)' : 'rgba(239,68,68,0.35)';

          const coinIcon = p.symbol.startsWith('BTC') ? '₿' : (p.symbol.startsWith('ETH') ? 'Ξ' : (p.symbol.startsWith('SOL') ? '◎' : (p.symbol.startsWith('XAU') || p.symbol.startsWith('PAXG') ? '🏆' : '⚡')));



          return `

            <div class="active-pos-card" style="flex:1; min-width:240px; border-left: 3px solid ${isProfit ? 'var(--emerald)' : 'var(--rose)'};">

              <div style="display:flex; justify-content:space-between; align-items:center;">

                <div style="display:flex; align-items:center; gap:6px;">

                  <span style="font-size:14px; font-weight:800; color:#fff;">${coinIcon} ${p.symbol}</span>

                  <span class="badge" style="padding:1px 6px; font-size:10px; background:${sideBg}; color:${sideColor}; border:1px solid ${sideBorder}; font-weight:800;">${p.side} ${p.leverage || 15}x</span>

                </div>

                ${p.tp1_hit ? '<span class="badge" style="background:rgba(245,158,11,0.2); color:#fbbf24; border:1px solid rgba(245,158,11,0.4); font-size:9px; font-weight:800;">BE STOP ACTIVE</span>' : '<span style="font-size:10px; color:var(--text-muted); font-family:var(--font-mono);">' + (p.notional_usd ? `$${Number(p.notional_usd).toLocaleString()} Notional` : '$20,000 Notional') + '</span>'}

              </div>



              <div style="display:flex; justify-content:space-between; align-items:baseline; margin-top:3px;">

                <div style="font-size:11px; color:var(--text-muted);">

                  Entry: <b style="color:#e2e8f0; font-family:var(--font-mono);">$${entryPx}</b> ➔ Mark: <b style="color:#fff; font-family:var(--font-mono);">$${curPx}</b>

                </div>

              </div>



              <div style="display:flex; justify-content:space-between; align-items:center; margin-top:3px; padding-top:4px; border-top:1px solid rgba(255,255,255,0.06);">

                <span style="font-size:10px; color:var(--text-muted);">Unrealized PnL:</span>

                <span style="font-size:13px; font-weight:800; font-family:var(--font-mono); color:${pnlColor};">

                  ${pnlSign}$${uPnlUsd.toFixed(2)} (${pnlSign}${uPnlPct.toFixed(1)}% on margin)

                </span>

              </div>

            </div>

          `;

        }).join('');

      }
    }

    // --- RENDER DEDICATED FULL OPEN POSITIONS TABLE DESK ---
    const openTableBody = document.getElementById('lsOpenPositionsTableBody');
    const openPosTabCount = document.getElementById('lsOpenPosTabCount');
    const positions = data.open_positions || [];

    if (openPosTabCount) {
      openPosTabCount.textContent = positions.length;
    }

    if (openTableBody) {
      if (positions.length === 0) {
        openTableBody.innerHTML = `
          <tr class="feed-placeholder-row">
            <td colspan="10" style="text-align:center; padding:28px; color:var(--text-muted); font-style:italic;">
              ${isAutopilotRunning ? '⚡ Autopilot is scanning for setups across Gold & Binance Universe... No open positions currently.' : 'Autopilot is currently stopped. Click "▶ START AUTOPILOT" to begin.'}
            </td>
          </tr>
        `;
      } else {
        openTableBody.innerHTML = positions.map(p => {
          const uPnlUsd = (typeof p.unrealized_pnl_usd === 'number') ? p.unrealized_pnl_usd : 0.0;
          const uPnlPct = (typeof p.unrealized_pnl_pct === 'number') ? p.unrealized_pnl_pct : 0.0;
          const isProfit = (uPnlUsd >= 0);
          const pnlColor = isProfit ? 'var(--emerald)' : 'var(--rose)';
          const pnlSign = isProfit ? '+' : '';
          
          const isLong = (p.side === 'LONG' || p.side === 'BUY');
          const sideColor = isLong ? '#34d399' : '#f87171';
          const sideBg = isLong ? 'rgba(16,185,129,0.15)' : 'rgba(239,68,68,0.15)';
          const sideBorder = isLong ? 'rgba(16,185,129,0.35)' : 'rgba(239,68,68,0.35)';

          const coinIcon = p.symbol.startsWith('BTC') ? '₿' : (p.symbol.startsWith('ETH') ? 'Ξ' : (p.symbol.startsWith('SOL') ? '◎' : (p.symbol.startsWith('XAU') || p.symbol.startsWith('PAXG') ? '🏆' : '⚡')));

          const fmtPx = (val) => {
            if (typeof val !== 'number') return '---';
            if (val >= 1000) return '$' + val.toLocaleString(undefined, {minimumFractionDigits: 2, maximumFractionDigits: 2});
            if (val >= 1) return '$' + val.toFixed(4);
            return '$' + val.toFixed(6);
          };

          const entryPx = fmtPx(p.entry_price);
          const curPx = fmtPx(p.current_price);
          const slPx = fmtPx(p.stop_loss);
          const tp1Px = fmtPx(p.tp1);
          const tp2Px = fmtPx(p.tp2);

          const notional = p.notional_usd ? `$${Math.round(p.notional_usd).toLocaleString()}` : '$20,000';
          const margin = p.margin_collateral_usd ? `$${Math.round(p.margin_collateral_usd).toLocaleString()}` : '$2,000';

          const statusBadge = p.tp1_hit
            ? `<span class="badge" style="background:rgba(245,158,11,0.2); color:#fbbf24; border:1px solid rgba(245,158,11,0.4); font-size:10px; font-weight:800;">🛡️ BE LOCKED (+0.80%)</span>`
            : `<span class="badge" style="background:rgba(59,130,246,0.15); color:#60a5fa; border:1px solid rgba(59,130,246,0.4); font-size:10px; font-weight:700;">HUNTING TP1 (${Math.round(p.conviction || 90)}% Conv)</span>`;

          return `
            <tr style="border-bottom: 1px solid rgba(255,255,255,0.04); transition: background 0.15s ease;" onmouseover="this.style.background='rgba(255,255,255,0.03)'" onmouseout="this.style.background='transparent'">
              <td style="padding:10px 12px;">
                <div style="display:flex; align-items:center; gap:8px;">
                  <span style="font-size:16px;">${coinIcon}</span>
                  <div>
                    <b style="color:#fff; font-size:13px;">${p.symbol}</b>
                    <div style="font-size:10px; color:var(--text-muted);">${(p.symbol.startsWith('XAU') || p.symbol.startsWith('PAXG')) ? 'Gold Market Perp' : 'Binance Crypto Perp'}</div>
                  </div>
                </div>
              </td>
              <td style="padding:10px 12px;">
                <span class="badge" style="padding:2px 8px; font-size:11px; background:${sideBg}; color:${sideColor}; border:1px solid ${sideBorder}; font-weight:800;">
                  ${isLong ? 'LONG' : 'SHORT'} ${p.leverage || 10}x
                </span>
              </td>
              <td style="padding:10px 12px; font-family:var(--font-mono); color:#cbd5e1; font-weight:600;">${entryPx}</td>
              <td style="padding:10px 12px; font-family:var(--font-mono); color:#fff; font-weight:700;">${curPx}</td>
              <td style="padding:10px 12px; font-family:var(--font-mono);">
                <div style="color:#fff; font-weight:700;">${notional}</div>
                <div style="font-size:10px; color:var(--text-muted);">Margin: ${margin}</div>
              </td>
              <td style="padding:10px 12px; font-family:var(--font-mono); color:#fca5a5;">${slPx}</td>
              <td style="padding:10px 12px; font-family:var(--font-mono);">
                <span style="color:#34d399; font-weight:600;">TP1: ${tp1Px}</span><br/>
                <span style="color:#6ee7b7; font-size:10px;">TP2: ${tp2Px}</span>
              </td>
              <td style="padding:10px 12px;">${statusBadge}</td>
              <td style="padding:10px 12px; font-family:var(--font-mono); font-size:13px; font-weight:800; color:${pnlColor};">
                ${pnlSign}$${uPnlUsd.toFixed(2)}
                <div style="font-size:11px; font-weight:700; opacity:0.9;">(${pnlSign}${uPnlPct.toFixed(1)}% ROE)</div>
              </td>
              <td style="padding:10px 12px;">
                <button type="button" class="btn" style="background:rgba(239,68,68,0.15); border:1px solid rgba(239,68,68,0.4); color:#fca5a5; font-size:11px; font-weight:700; padding:4px 10px; border-radius:6px; cursor:pointer; transition:all 0.15s ease;" onmouseover="this.style.background='rgba(239,68,68,0.35)'" onmouseout="this.style.background='rgba(239,68,68,0.15)'" onclick="closeAutopilotPosition('${p.symbol}')">
                  Close
                </button>
              </td>
            </tr>
          `;
        }).join('');
      }
    }

    if (!window.closeAutopilotPosition) {
      window.closeAutopilotPosition = async function(symbol) {
        if (!confirm(`Are you sure you want to market close ${symbol} position?`)) return;
        try {
          const res = await fetch('/api/autotrade/close', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ symbol })
          });
          const result = await res.json();
          if (result.success) {
            if (typeof showToast === 'function') showToast(`Successfully closed ${symbol} position`, 'success');
            pollAutopilotStatus();
          } else {
            if (typeof showToast === 'function') showToast(`Failed to close ${symbol}: ${result.error || 'Unknown error'}`, 'error');
          }
        } catch (err) {
          console.error('Error closing position:', err);
        }
      };
    }

    // --- RENDER CLOSED TRADES AUDIT LOG & HISTORICAL PNL ---
    const closedTableBody = document.getElementById('lsClosedTradesList');
    const closedTabCount = document.getElementById('lsClosedTradeCount');
    const closedTrades = data.closed_trades || [];
    const totalClosed = data.total_closed_trades_count || closedTrades.length;

    if (closedTabCount) {
      closedTabCount.textContent = totalClosed;
    }

    // Synchronize directional performance stats strip if present
    const lsNetRealizedPnl = document.getElementById('lsNetRealizedPnl');
    const lsWinRate = document.getElementById('lsWinRate');
    const lsPaperBalance = document.getElementById('lsPaperBalance');

    if (lsNetRealizedPnl && typeof data.total_realized_pnl_usd === 'number') {
      const pnlVal = data.total_realized_pnl_usd;
      lsNetRealizedPnl.textContent = (pnlVal >= 0 ? '+$' : '-$') + Math.abs(pnlVal).toFixed(2);
      lsNetRealizedPnl.style.color = pnlVal >= 0 ? 'var(--emerald)' : 'var(--rose)';
    }
    if (lsWinRate && typeof data.win_rate_pct === 'number') {
      lsWinRate.textContent = `${data.win_rate_pct.toFixed(1)}% (${data.wins_count || 0}/${totalClosed})`;
      lsWinRate.style.color = (data.win_rate_pct >= 50) ? 'var(--emerald)' : 'var(--amber)';
    }
    if (lsPaperBalance && data.account_balance_usd) {
      const effBalance = data.account_balance_usd + (data.total_realized_pnl_usd || 0.0);
      lsPaperBalance.textContent = `$${effBalance.toLocaleString(undefined, {minimumFractionDigits: 2, maximumFractionDigits: 2})}`;
    }

    if (closedTableBody) {
      if (closedTrades.length === 0) {
        closedTableBody.innerHTML = `
          <tr class="feed-placeholder-row">
            <td colspan="10" style="text-align:center; padding:28px; color:var(--text-muted); font-style:italic;">
              No closed trades yet in this session. Trades will appear here automatically as Stop Loss or Take Profit targets trigger.
            </td>
          </tr>
        `;
      } else {
        closedTableBody.innerHTML = closedTrades.map(t => {
          const pnlUsd = (typeof t.pnl_usd === 'number') ? t.pnl_usd : 0.0;
          const pnlPct = (typeof t.pnl_pct === 'number') ? t.pnl_pct : 0.0;
          const isWin = (pnlUsd >= 0);
          const pnlColor = isWin ? 'var(--emerald)' : 'var(--rose)';
          const pnlSign = isWin ? '+' : '';

          const isLong = (t.side === 'LONG' || t.side === 'BUY');
          const sideColor = isLong ? '#34d399' : '#f87171';
          const sideBg = isLong ? 'rgba(16,185,129,0.15)' : 'rgba(239,68,68,0.15)';
          const sideBorder = isLong ? 'rgba(16,185,129,0.35)' : 'rgba(239,68,68,0.35)';

          const sym = t.symbol || '---';
          const coinIcon = sym.startsWith('BTC') ? '₿' : (sym.startsWith('ETH') ? 'Ξ' : (sym.startsWith('SOL') ? '◎' : (sym.startsWith('XAU') || sym.startsWith('PAXG') ? '🏆' : '⚡')));

          const fmtPx = (val) => {
            if (typeof val !== 'number') return '---';
            if (val >= 1000) return '$' + val.toLocaleString(undefined, {minimumFractionDigits: 2, maximumFractionDigits: 2});
            if (val >= 1) return '$' + val.toFixed(4);
            return '$' + val.toFixed(6);
          };

          const entryPx = fmtPx(t.entry_price);
          const exitPx = fmtPx(t.exit_price);

          let durationStr = '---';
          if (t.duration_sec) {
            const m = Math.floor(t.duration_sec / 60);
            const s = Math.floor(t.duration_sec % 60);
            durationStr = m > 0 ? `${m}m ${s}s` : `${s}s`;
          }

          let reasonBadge = '';
          const r = t.exit_reason || '';
          if (r.includes('TP2') || r.includes('RUNNER')) {
            reasonBadge = `<span class="badge" style="background:rgba(16,185,129,0.2); color:#34d399; border:1px solid rgba(16,185,129,0.4); font-size:10px; font-weight:800;">🎯 TP2 RUNNER TARGET</span>`;
          } else if (r.includes('BREAK_EVEN') || r.includes('BE_STOP')) {
            reasonBadge = `<span class="badge" style="background:rgba(245,158,11,0.2); color:#fbbf24; border:1px solid rgba(245,158,11,0.4); font-size:10px; font-weight:800;">🛡️ BREAK-EVEN STOP</span>`;
          } else if (r.includes('ALPHA_DECAY') || r.includes('TIMEOUT')) {
            reasonBadge = `<span class="badge" style="background:rgba(148,163,184,0.2); color:#cbd5e1; border:1px solid rgba(148,163,184,0.3); font-size:10px; font-weight:700;">⏳ ALPHA DECAY EXIT</span>`;
          } else if (r.includes('MANUAL')) {
            reasonBadge = `<span class="badge" style="background:rgba(168,85,247,0.2); color:#c084fc; border:1px solid rgba(168,85,247,0.4); font-size:10px; font-weight:700;">✋ MANUAL CLOSE</span>`;
          } else {
            reasonBadge = `<span class="badge" style="background:rgba(239,68,68,0.2); color:#f87171; border:1px solid rgba(239,68,68,0.4); font-size:10px; font-weight:700;">🛑 STOP LOSS</span>`;
          }

          return `
            <tr style="border-bottom: 1px solid rgba(255,255,255,0.04); transition: background 0.15s ease;" onmouseover="this.style.background='rgba(255,255,255,0.03)'" onmouseout="this.style.background='transparent'">
              <td style="padding:9px 12px;">
                <div style="display:flex; align-items:center; gap:8px;">
                  <span style="font-size:15px;">${coinIcon}</span>
                  <div>
                    <b style="color:#fff; font-size:12px;">${sym}</b>
                    <div style="font-size:10px; color:var(--text-muted);">${(sym.startsWith('XAU') || sym.startsWith('PAXG')) ? 'Gold Perp' : 'Crypto Perp'}</div>
                  </div>
                </div>
              </td>
              <td style="padding:9px 12px; font-family:var(--font-mono); font-size:11px; color:#94a3b8;">
                ${t.closed_at || '---'}
              </td>
              <td style="padding:9px 12px;">
                <span class="badge" style="padding:2px 8px; font-size:10px; background:${sideBg}; color:${sideColor}; border:1px solid ${sideBorder}; font-weight:800;">
                  ${isLong ? 'LONG' : 'SHORT'} 10x
                </span>
              </td>
              <td style="padding:9px 12px; font-family:var(--font-mono); color:#cbd5e1;">${entryPx}</td>
              <td style="padding:9px 12px; font-family:var(--font-mono); color:#fff; font-weight:600;">${exitPx}</td>
              <td style="padding:9px 12px; font-family:var(--font-mono); color:var(--text-muted); font-size:11px;">$2,000</td>
              <td style="padding:9px 12px; font-family:var(--font-mono); font-size:12px; font-weight:800; color:${pnlColor};">
                ${pnlSign}$${pnlUsd.toFixed(2)}
              </td>
              <td style="padding:9px 12px; font-family:var(--font-mono); font-size:11px; font-weight:700; color:${pnlColor};">
                ${pnlSign}${pnlPct.toFixed(1)}% ROE
              </td>
              <td style="padding:9px 12px; font-family:var(--font-mono); font-size:11px; color:#94a3b8;">
                ${durationStr}
              </td>
              <td style="padding:9px 12px;">
                ${reasonBadge}
              </td>
            </tr>
          `;
        }).join('');
      }
    }


    // --- RENDER REAL-TIME AI DECISION RADAR & SETUP STREAM ---
    const dec = data.latest_decision;
    if (dec) {
      const rTarget = document.getElementById('radarTargetAsset');
      const rAction = document.getElementById('radarActionText');
      const rConv = document.getElementById('radarConvictionText');
      const rRr = document.getElementById('radarRrRatio');
      const rEntry = document.getElementById('radarEntryPx');
      const rSl = document.getElementById('radarSlPx');
      const rTp1 = document.getElementById('radarTp1Px');
      const rTp2 = document.getElementById('radarTp2Px');
      const rCat = document.getElementById('radarCatalystText');
      const rTime = document.getElementById('radarUpdateTime');

      if (rTarget) rTarget.textContent = dec.symbol || 'XAUUSDT (Gold)';
      if (rAction) {
        const action = dec.action || (dec.signal === 'LONG' ? 'LONG STRIKE 🟢' : (dec.signal === 'SHORT' ? 'SHORT STRIKE 🔴' : 'HUNTING BREAKOUTS ⏳'));
        rAction.textContent = action;
        rAction.style.color = action.includes('LONG') ? 'var(--emerald)' : (action.includes('SHORT') ? 'var(--rose)' : 'var(--amber)');
      }
      if (rConv) {
        const score = dec.conviction || dec.confidence_score || 4.8;
        const ev = dec.ev_usd || 580.0;
        rConv.textContent = `Conviction: ${score}/5.0 | EV: +$${Number(ev).toFixed(2)}`;
      }
      if (rRr) rRr.textContent = `1 : ${(dec.rr_ratio || 2.5).toFixed(1)} R:R`;
      if (rEntry) rEntry.textContent = `$${Number(dec.entry_price || 0).toLocaleString(undefined, {minimumFractionDigits: 2, maximumFractionDigits: 2})}`;
      if (rSl) rSl.textContent = `$${Number(dec.stop_loss || 0).toLocaleString(undefined, {minimumFractionDigits: 2, maximumFractionDigits: 2})}`;
      if (rTp1) rTp1.textContent = `$${Number(dec.tp1 || 0).toLocaleString(undefined, {minimumFractionDigits: 2, maximumFractionDigits: 2})}`;
      if (rTp2) rTp2.textContent = `$${Number(dec.tp2 || 0).toLocaleString(undefined, {minimumFractionDigits: 2, maximumFractionDigits: 2})}`;
      if (rCat) rCat.textContent = dec.catalyst || dec.rationale || 'Evaluating Gold & Top 20 Binance momentum, book imbalance and volatility compression...';
      if (rTime) rTime.textContent = 'Updated: ' + new Date().toLocaleTimeString();
    }

    // --- RENDER REAL-TIME COGNITIVE THOUGHT STREAM ---
    const thoughtConsole = document.getElementById('radarThoughtConsole');
    if (thoughtConsole && Array.isArray(data.thought_stream) && data.thought_stream.length > 0) {
      thoughtConsole.innerHTML = data.thought_stream.map(t => {
        let color = '#94a3b8';
        if (t.includes('ENTRY') || t.includes('LONG') || t.includes('TP1') || t.includes('PROFIT') || t.includes('EXECUTED')) color = '#34d399';
        else if (t.includes('SHORT') || t.includes('SL') || t.includes('LOSS')) color = '#f87171';
        else if (t.includes('GOLD') || t.includes('XAU')) color = '#fbbf24';
        else if (t.includes('SCAN') || t.includes('PASS') || t.includes('WAIT')) color = '#818cf8';
        return `<div style="color:${color}; font-family:var(--font-mono);">${t}</div>`;
      }).join('');
      thoughtConsole.scrollTop = thoughtConsole.scrollHeight;
    }


  } catch (err) {
    console.warn('Autopilot status poll error:', err);
  }

}



async function toggleMasterAutopilot() {
  await toggleAutopilotTrader();
}

async function toggleAutopilotTrader() {

  const willRun = !isAutopilotRunning;

  try {

    const res = await fetch('/api/autotrade/toggle', {

      method: 'POST',

      headers: { 'Content-Type': 'application/json' },

      body: JSON.stringify({ enabled: willRun })

    });

    const result = await res.json();

    if (result.success) {

      await pollAutopilotStatus();

    } else {

      alert('Error toggling autopilot: ' + (result.error || 'Unknown error'));

    }

  } catch (e) {

    alert('Network error: ' + e.message);

  }

}



// Auto-poll autopilot status every 3 seconds

setInterval(pollAutopilotStatus, 3000);

setTimeout(pollAutopilotStatus, 600);



// Default to Jev Multi-Asset Terminal on page load

setTimeout(() => {

  if (typeof switchTab === 'function') switchTab('long-short');

}, 100);

