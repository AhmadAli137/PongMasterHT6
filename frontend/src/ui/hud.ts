// DOM HUD overlay: score, combo, timer, tracking status, prompts, diagnostics.

import type { ImpactMsg, RallyMsg, StateMsg } from "../types";

export class Hud {
  private root: HTMLDivElement;
  private scoreEl!: HTMLDivElement;
  private comboEl!: HTMLDivElement;
  private timerEl!: HTMLDivElement;
  private centerEl!: HTMLDivElement;
  private trackEl!: HTMLDivElement;
  private diagEl!: HTMLDivElement;
  private chargeEl!: HTMLDivElement;
  private chargeFillEl!: HTMLDivElement;
  private diagVisible = false;
  private connected = false;

  constructor(parent: HTMLElement) {
    this.root = document.createElement("div");
    this.root.innerHTML = TEMPLATE;
    Object.assign(this.root.style, { position: "fixed", inset: "0", pointerEvents: "none" } as CSSStyleDeclaration);
    parent.appendChild(this.root);
    injectStyles();
    this.scoreEl = this.root.querySelector("#hud-score")!;
    this.comboEl = this.root.querySelector("#hud-combo")!;
    this.timerEl = this.root.querySelector("#hud-timer")!;
    this.centerEl = this.root.querySelector("#hud-center")!;
    this.trackEl = this.root.querySelector("#hud-track")!;
    this.diagEl = this.root.querySelector("#hud-diag")!;
    this.chargeEl = this.root.querySelector("#hud-charge")!;
    this.chargeFillEl = this.chargeEl.querySelector("div")!;

    window.addEventListener("keydown", (e) => {
      if (e.key === "d" || e.key === "D") {
        this.diagVisible = !this.diagVisible;
        this.diagEl.style.display = this.diagVisible ? "block" : "none";
      }
    });
  }

  setConnected(c: boolean): void {
    this.connected = c;
  }

  update(s: StateMsg): void {
    this.scoreEl.textContent = s.score.toLocaleString();
    this.comboEl.textContent = s.combo > 1 ? `${s.combo}× COMBO` : "";
    const secs = Math.max(0, Math.ceil(s.remainingMs / 1000));
    this.timerEl.textContent = `${secs}`;

    const mode = s.controlMode === "MOUSE" ? " · MOUSE" : "";
    const stance = s.controlMode === "MOUSE" && s.stance === "BACKHAND" ? " · BACKHAND" : "";
    this.trackEl.textContent = this.connected
      ? `TRACK ${s.paddle.trackingState}${mode}${stance}`
      : "OFFLINE";
    this.trackEl.className = `hud-track ${trackClass(this.connected ? s.paddle.trackingState : "LOST")}`;

    // center prompts by game state
    const g = s.gameState;
    if (g === "READY") {
      this.centerEl.innerHTML = s.balanceMode
        ? prompt("BALANCE MODE", "Move the mouse to tilt the tray and roll the ball — <b>B</b> to exit")
        : prompt("READY", "Press <b>SPACE</b> to start · move mouse to take control<br>swing fast for power · right-drag = wrist aim · right-click = backhand · click = strike/toss");
    }
    else if (g === "WAIT_FOR_PADDLE") this.centerEl.innerHTML = prompt("CONNECTING", "Waiting for paddle…");
    else if (g === "CALIBRATION") this.centerEl.innerHTML = calibration(s.calibrationProgress ?? 0);
    else if (g === "PAUSED") this.centerEl.innerHTML = prompt("PAUSED", "Press <b>ESC</b> to resume");
    else if (g === "COUNTDOWN") this.centerEl.innerHTML = `<div class="hud-count">${Math.ceil(s.countdown) || "GO"}</div>`;
    else if (g === "RESULTS") this.centerEl.innerHTML = results(s);
    else if (g === "PADDLE_DISCONNECTED") this.centerEl.innerHTML = prompt("PADDLE LOST", "Reconnecting…");
    else if (g === "PLAYING" && s.playerServe) {
      this.centerEl.innerHTML =
        s.playerServe.phase === "AIM"
          ? `<div class="hud-serve">YOUR SERVE</div><div class="hud-sub">Position the paddle, click to toss</div>`
          : `<div class="hud-serve">${Math.ceil(s.playerServe.dropInMs / 500)}</div><div class="hud-sub">swing when it drops…</div>`;
    }
    else this.centerEl.innerHTML = "";
  }

  onImpact(i: ImpactMsg): void {
    const el = document.createElement("div");
    el.className = "hud-pop";
    el.textContent = i.ballType === "AVOID" ? `${i.scoreDelta}` : `${i.quality} +${i.scoreDelta}`;
    el.style.color = i.quality === "PERFECT" ? "#00e0ff" : i.ballType === "AVOID" ? "#ff3366" : "#ffffff";
    this.root.appendChild(el);
    setTimeout(() => el.remove(), 800);
  }

  onRally(r: RallyMsg): void {
    const text: Record<string, string> = {
      IN: `IN! +${r.scoreDelta}`,
      WINNER: `WINNER! +${r.scoreDelta}`,
      NET: "NET",
      OUT: "OUT",
      OWN_SIDE: "NO CLEAR",
      MISS: "MISS",
    };
    const color: Record<string, string> = {
      IN: "#3dfc9a", WINNER: "#ffd23f",
      NET: "#ff5566", OUT: "#ff5566", OWN_SIDE: "#ff5566", MISS: "#ff5566",
    };
    const el = document.createElement("div");
    el.className = "hud-pop hud-pop-rally";
    el.textContent = text[r.outcome] ?? r.outcome;
    el.style.color = color[r.outcome] ?? "#ffffff";
    this.root.appendChild(el);
    setTimeout(() => el.remove(), 900);
  }

  /** 0 hides the meter; >0 shows fill; goes hot near full charge. */
  setCharge(v: number): void {
    if (v <= 0) {
      this.chargeEl.style.display = "none";
      return;
    }
    this.chargeEl.style.display = "block";
    this.chargeFillEl.style.width = `${Math.round(v * 100)}%`;
    this.chargeFillEl.style.background = v >= 1 ? "#ff8030" : "#00e0ff";
    this.chargeFillEl.style.boxShadow = v >= 1 ? "0 0 14px #ff8030" : "0 0 10px #00e0ff";
  }

  updateDiagnostics(m: Record<string, unknown>): void {
    if (!this.diagVisible) return;
    this.diagEl.innerHTML = Object.entries(m)
      .map(([k, v]) => `<div><span>${k}</span><b>${v}</b></div>`)
      .join("");
  }
}

function trackClass(state: string): string {
  return state === "GOOD" ? "ok" : state === "DEGRADED" ? "warn" : "bad";
}

function prompt(title: string, sub: string): string {
  return `<div class="hud-title">${title}</div><div class="hud-sub">${sub}</div>`;
}

function calibration(progress: number): string {
  const pct = Math.round(progress * 100);
  // dashed outline = where to hold the paddle; bar = hold-steady progress
  return `<div class="hud-title">CALIBRATING</div>
    <div class="hud-outline"></div>
    <div class="hud-sub">Hold the paddle steady inside the outline</div>
    <div class="hud-progress"><div style="width:${pct}%"></div></div>`;
}

function results(s: StateMsg): string {
  return `<div class="hud-title">RESULTS</div>
    <div class="hud-results">
      <div><span>Score</span><b>${s.score.toLocaleString()}</b></div>
      <div><span>Best combo</span><b>${s.bestCombo}×</b></div>
      <div><span>Accuracy</span><b>${Math.round(s.accuracy * 100)}%</b></div>
    </div>
    <div class="hud-sub">Press <b>SPACE</b> to play again</div>`;
}

const TEMPLATE = `
  <div class="hud-topbar">
    <div id="hud-score" class="hud-score">0</div>
    <div id="hud-timer" class="hud-timer">75</div>
    <div id="hud-track" class="hud-track bad">OFFLINE</div>
  </div>
  <div id="hud-combo" class="hud-combo"></div>
  <div id="hud-center" class="hud-center"></div>
  <div id="hud-charge" class="hud-charge"><div></div></div>
  <div id="hud-diag" class="hud-diag"></div>
`;

function injectStyles(): void {
  if (document.getElementById("hud-styles")) return;
  const style = document.createElement("style");
  style.id = "hud-styles";
  style.textContent = `
    .hud-topbar { position:absolute; top:24px; left:0; right:0; display:flex; justify-content:space-between; align-items:center; padding:0 40px; font-weight:700; }
    .hud-score { font-size:52px; color:#fff; text-shadow:0 0 20px #00e0ff88; }
    .hud-timer { font-size:40px; color:#00e0ff; }
    .hud-track { font-size:15px; padding:6px 12px; border-radius:20px; letter-spacing:1px; }
    .hud-track.ok { background:#0a3; color:#bfffd0; }
    .hud-track.warn { background:#b80; color:#ffe9b0; }
    .hud-track.bad { background:#a03; color:#ffc0cc; }
    .hud-combo { position:absolute; top:100px; left:0; right:0; text-align:center; font-size:34px; font-weight:800; color:#ffd23f; text-shadow:0 0 18px #ffd23f88; }
    .hud-center { position:absolute; inset:0; display:flex; flex-direction:column; gap:14px; align-items:center; justify-content:center; text-align:center; }
    .hud-title { font-size:44px; font-weight:800; color:#fff; letter-spacing:2px; }
    .hud-sub { font-size:20px; color:#9fc0e0; }
    .hud-count { font-size:120px; font-weight:900; color:#00e0ff; text-shadow:0 0 40px #00e0ff; }
    .hud-results { display:flex; gap:34px; margin:10px 0; }
    .hud-results div, .hud-diag div { display:flex; flex-direction:column; }
    .hud-results span { font-size:14px; color:#7f9fbf; }
    .hud-results b { font-size:30px; color:#fff; }
    .hud-pop { position:absolute; top:44%; left:50%; transform:translateX(-50%); font-size:26px; font-weight:800; animation:pop 0.8s ease-out forwards; }
    .hud-pop-rally { top:34%; font-size:32px; letter-spacing:2px; }
    @keyframes pop { 0%{opacity:0; transform:translate(-50%,10px) scale(0.8);} 20%{opacity:1;} 100%{opacity:0; transform:translate(-50%,-40px) scale(1.1);} }
    .hud-progress { width:300px; height:10px; background:#12263f; border-radius:6px; overflow:hidden; }
    .hud-progress div { height:100%; background:#00e0ff; border-radius:6px; transition:width 0.12s linear; box-shadow:0 0 12px #00e0ff; }
    .hud-outline { width:160px; height:160px; border:3px dashed #00e0ff88; border-radius:14px; animation:outline-pulse 1.2s ease-in-out infinite; }
    @keyframes outline-pulse { 0%,100%{border-color:#00e0ff55;} 50%{border-color:#00e0ffcc;} }
    .hud-charge { display:none; position:absolute; bottom:44px; left:50%; transform:translateX(-50%); width:220px; height:9px; background:#12263f; border-radius:6px; overflow:hidden; }
    .hud-charge div { height:100%; width:0; border-radius:6px; transition:width 0.05s linear; }
    .hud-serve { font-size:54px; font-weight:900; color:#ffd23f; text-shadow:0 0 24px #ffd23f66; letter-spacing:3px; }
    .hud-diag { display:none; position:absolute; bottom:24px; left:24px; font-family:monospace; font-size:13px; color:#8fd; background:#0008; padding:12px 16px; border-radius:8px; }
    .hud-diag span { color:#68a; margin-right:8px; }
  `;
  document.head.appendChild(style);
}
