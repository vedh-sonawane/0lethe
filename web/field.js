/* 0/lethe --- the field.
 *
 * Darkness is everything forgotten. Light exists only where the machine
 * recovered something from real evidence. Each mark is a phonautogram scratch:
 * a little waveform carved out of soot, unique to the thing it stands for.
 * Recovered things are crisp and bone-white; ghosts tremble, faint and
 * unfinished --- the shape of what is missing. You move through space by
 * dragging, through time along the scratch at the bottom. Nothing explains
 * itself. You find it the way it was always meant to be found: by looking.
 */
'use strict';

const MONO  = '11px "SFMono-Regular","Consolas","Liberation Mono",ui-monospace,monospace';
const MONO_S= '10px "SFMono-Regular","Consolas","Liberation Mono",ui-monospace,monospace';
const SERIF = '"Iowan Old Style","Palatino Linotype","Palatino","Georgia",serif';

const BONE   = [233, 227, 213];   // recovered light, warm as old paper
const ASH    = [150, 146, 138];   // present-but-incomplete
const SOOT   = '#060504';

const canvas = document.getElementById('field');
const ctx = canvas.getContext('2d', { alpha: false });
let W = 0, H = 0, DPR = 1;

// ---- state ----------------------------------------------------------------
let marks = [];
const cam = { x: 0, y: 0, scale: 1 };          // field-space is roughly -1..1
const camTarget = { x: 0, y: 0, scale: 1 };    // the machine eases toward this (fly-to)
const markById = new Map();
let tMin = 1994, tMax = 2026, tNow = 2026;      // the year the operator is standing in
let focusId = null, detail = null, detailAlpha = 0;
const pointer = { x: -1e4, y: -1e4, down: false, mode: null, mx: 0, my: 0 };
let hoverId = null;
let revealFrames = 0;                            // the field inscribes itself in, mark by mark

// ---- soot texture (drawn once, physical grain) ----------------------------
let soot = null;
function buildSoot() {
    soot = document.createElement('canvas');
    soot.width = W; soot.height = H;
    const g = soot.getContext('2d');
    g.fillStyle = SOOT; g.fillRect(0, 0, W, H);
    const img = g.getImageData(0, 0, W, H), d = img.data;
    for (let i = 0; i < d.length; i += 4) {
        const n = (Math.random() * 22 - 9) | 0;     // fine soot grain
        d[i] += n; d[i + 1] += n; d[i + 2] += n;
    }
    g.putImageData(img, 0, 0);
    // a few deeper pits and faint lifted flecks, like real soot on paper
    for (let k = 0; k < (W * H) / 7000; k++) {
        const x = Math.random() * W, y = Math.random() * H, r = Math.random() * 1.6;
        g.fillStyle = Math.random() < 0.5 ? 'rgba(0,0,0,0.5)' : 'rgba(60,56,50,0.22)';
        g.beginPath(); g.arc(x, y, r, 0, 7); g.fill();
    }
}

function resize() {
    DPR = Math.min(window.devicePixelRatio || 1, 2);
    W = Math.floor(innerWidth * DPR); H = Math.floor(innerHeight * DPR);
    canvas.width = W; canvas.height = H;
    canvas.style.width = innerWidth + 'px'; canvas.style.height = innerHeight + 'px';
    buildSoot();
}
addEventListener('resize', resize);

// ---- deterministic per-ghost randomness (stable scratch shapes) -----------
function rng(seed) {
    let s = seed >>> 0;
    return () => { s = (s + 0x6D2B79F5) | 0; let t = Math.imul(s ^ (s >>> 15), 1 | s);
        t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t; return ((t ^ (t >>> 14)) >>> 0) / 4294967296; };
}

// ---- geometry -------------------------------------------------------------
function viewExtent() { return Math.min(W, H) * 0.42; }
function toScreen(fx, fy) {
    const e = viewExtent() * cam.scale;
    return [W / 2 + (fx - cam.x) * e, H / 2 + (fy - cam.y) * e];
}
function fromScreen(sx, sy) {
    const e = viewExtent() * cam.scale;
    return [(sx - W / 2) / e + cam.x, (sy - H / 2) / e + cam.y];
}
function yearOf(iso) { return iso ? +iso.slice(0, 4) + (+iso.slice(5, 7) - 1) / 12 : null; }

// ALL OF TIME on one axis: present on the right, antiquity compressed to the
// left by a log of "years before present", so 2009 and 500 BC both have room.
const PRESENT = 2027;
function tToX(year) {
    const u = Math.log10(1 + Math.max(0, PRESENT - year));
    const uMax = Math.log10(1 + (PRESENT - tMin)) || 1;
    return axis.x1 - (axis.x1 - axis.x0) * (u / uMax);
}
function xToT(x) {
    const uMax = Math.log10(1 + (PRESENT - tMin)) || 1;
    const u = (axis.x1 - x) / (axis.x1 - axis.x0) * uMax;
    return PRESENT - (Math.pow(10, u) - 1);
}
function markEpoch(m) { return (m.epoch_year != null) ? m.epoch_year : yearOf(m.existed_from); }
function fmtYear(y) {
    y = Math.round(y);
    if (y <= 0) return (1 - y) + ' bc';
    if (y < 1000) return y + ' ad';
    return '' + y;
}

// life phase of a mark at the current time-standpoint
// 'unborn' | 'alive' | 'uncertain' | 'gone' | 'ghost' | 'at_risk' | 'preserved'
function phase(m) {
    if (m.status === 'at_risk' || m.status === 'preserved') return { kind: m.status, p: 1 };
    const e = markEpoch(m);
    if (e == null) return { kind: m.status === 'reconstructed' ? 'gone' : 'ghost', p: 1 };
    if (m.kind === 'text' || m.kind === 'lost_work') {   // a work: its era, then lost
        if (tNow < e - 60) return { kind: 'unborn', p: 0 };
        if (tNow <= e + 120) return { kind: 'alive', p: 1 };
        return { kind: 'gone', p: 1 };
    }
    if (m.kind === 'place') {                  // a building: stands from built to demolished
        const until = yearOf(m.existed_until);
        if (tNow < e - 4) return { kind: 'unborn', p: 0 };
        if (until != null && tNow > until) return { kind: 'gone', p: 1 };
        return { kind: 'alive', p: 1 };
    }
    const until = yearOf(m.existed_until);
    if (tNow < e - 0.05) return { kind: 'unborn', p: 0 };
    if (until != null && tNow <= until + 0.08) return { kind: 'alive', p: 1 };
    if (tNow <= tMax) return { kind: 'uncertain', p: 1 };
    return { kind: 'gone', p: 1 };
}

// ---- the scratch: one phonautogram mark -----------------------------------
function scratch(x, y, m, now) {
    const r = rng(m.id * 2654435761);
    const conf = m.confidence || 0.25;
    const w = Math.max(0, Math.min(1, m.worth || 0));   // significance of the loss
    const ph = phase(m);
    if (ph.kind === 'unborn') return;

    // inscription: each mark scratches in out of the dark, staggered, once
    const rvDelay = ((m.id * 2654435761 >>> 0) % 100000) / 100000 * 55;
    const rv = Math.max(0, Math.min(1, (revealFrames - rvDelay) / 22));
    if (rv <= 0.001) return;

    const atrisk = ph.kind === 'at_risk';
    const preserved = ph.kind === 'preserved';
    const risk = Math.max(0, Math.min(1, m.risk || 0));
    const alive = ph.kind === 'alive';
    const recovered = m.status === 'reconstructed';
    const strong = (recovered && alive) || preserved;

    // dimensions scale with worth (loss) or risk (fragility), and GROW with
    // convergence: the more distinct sites point at a void, the larger it looms.
    const conv = Math.min(1, (m.reference_count || 0) / 6);
    const sizeW = 0.5 + 0.5 * (atrisk || preserved ? risk : w) + 0.7 * conv;
    const base = (40 + conf * 92) * DPR * sizeW;
    const len = base * (0.62 + 0.8 * r()) * Math.min(1.9, cam.scale ** 0.35);
    const amp = (6 + conf * 22) * DPR * sizeW * Math.min(1.9, cam.scale ** 0.35);
    const freq = 2 + Math.floor(r() * 4), f2 = freq + 1 + Math.floor(r() * 3);
    const ph1 = r() * 7, ph2 = r() * 7;

    // presence: bright & crisp when strong; faint & trembling otherwise.
    let a = strong ? 0.96 : (recovered ? 0.7 : 0.42);
    if (ph.kind === 'uncertain') a *= 0.72;
    if (atrisk) {
        // a guttering flame: visible but unstable, flickering toward going out.
        a = (0.3 + 0.45 * risk) * (0.6 + 0.4 * Math.sin(now * 0.006 + m.id * 2.3));
    } else {
        a *= (0.52 + 0.48 * w);
        a = Math.min(1, a + 0.28 * conv);     // heavily-referenced voids burn brighter
    }
    if (hoverId === m.id || focusId === m.id) a = Math.min(1, a + 0.3);
    a *= rv;                                      // fade in as it inscribes
    const col = strong ? BONE : ASH;

    // ghosts tremble --- unfinished, present but incomplete
    const tremble = strong ? 0 : (recovered ? 0.6 : 1.4);
    const tx = tremble * Math.sin(now * 0.0017 + m.id) * (0.8 + 0.5 * r());
    const ty = tremble * Math.cos(now * 0.0021 + m.id * 1.7);

    const N = 48;
    const envPow = strong ? 1 : 1.8;      // ghosts fray at the ends
    // underscratch: a faint wide echo, 'carved out of black' without any neon
    for (let pass = 0; pass < (strong ? 2 : 1); pass++) {
        ctx.beginPath();
        for (let i = 0; i <= N; i++) {
            const u = i / N, px = x - len / 2 + u * len + tx;
            const env = Math.sin(Math.PI * u) ** envPow;
            let wob = Math.sin(u * freq * 6.283 + ph1) * amp
                    + Math.sin(u * f2 * 6.283 + ph2) * amp * 0.45;
            if (!strong) wob += (r() - 0.5) * amp * 0.9;     // fray
            const py = y + wob * env + ty;
            i ? ctx.lineTo(px, py) : ctx.moveTo(px, py);
        }
        const [R, G, B] = col;
        if (pass === 0 && strong) {
            ctx.strokeStyle = `rgba(${R},${G},${B},${a * 0.16})`;
            ctx.lineWidth = 4.0 * DPR;
        } else {
            ctx.strokeStyle = `rgba(${R},${G},${B},${a})`;
            ctx.lineWidth = (strong ? 1.5 : 1.0) * DPR;
            if (!strong) ctx.setLineDash([2.5 * DPR, 3.5 * DPR]);
        }
        ctx.lineJoin = 'round'; ctx.stroke(); ctx.setLineDash([]);
    }

    // the still point: where the needle rests. brighter for the recovered.
    ctx.beginPath(); ctx.arc(x + tx, y + ty, (strong ? 1.5 : 1.0) * DPR, 0, 7);
    ctx.fillStyle = `rgba(${col[0]},${col[1]},${col[2]},${Math.min(1, a + 0.2)})`; ctx.fill();

    m._sx = x; m._sy = y; m._r = Math.max(len / 2, 16); // hit area
}

// ---- the evidence constellation (canvas) ----------------------------------
// The readout itself is real, selectable text (see renderReadout); only the
// threads from a focused mark out to its shadows are drawn here.
function drawConstellation(now) {
    detailAlpha += ((focusId ? 1 : 0) - detailAlpha) * 0.14;
    if (!detail || detailAlpha < 0.02) return;
    const A = detailAlpha;
    const g = detail.ghost;
    const fm = marks.find(m => m.id === (focusId || g.id));
    if (!fm || fm._sx == null) return;
    const sh = detail.shadows || [];
    const R0 = 64 * DPR + (fm._r > 0 ? fm._r : 20 * DPR);
    sh.forEach((s, i) => {
        const ang = (i / Math.max(1, sh.length)) * 6.283 - 1.2;
        const rr = R0 + (s.kind === 'citation' ? 46 * DPR : 0) + (i % 3) * 10 * DPR
                 + 8 * DPR * Math.sin(now * 0.001 + i);          // a slow breathing
        const ex = fm._sx + Math.cos(ang) * rr, ey = fm._sy + Math.sin(ang) * rr;
        ctx.beginPath(); ctx.moveTo(fm._sx, fm._sy); ctx.lineTo(ex, ey);
        ctx.strokeStyle = `rgba(${ASH[0]},${ASH[1]},${ASH[2]},${0.11 * A})`;
        ctx.lineWidth = 0.6 * DPR; ctx.stroke();
        ctx.beginPath(); ctx.arc(ex, ey, (s.kind === 'snapshot' ? 1.3 : 1.9) * DPR, 0, 7);
        ctx.fillStyle = `rgba(${BONE[0]},${BONE[1]},${BONE[2]},${0.55 * A})`; ctx.fill();
        if (s.captured_at) {
            ctx.font = MONO_S; ctx.textAlign = 'center';
            ctx.fillStyle = `rgba(${ASH[0]},${ASH[1]},${ASH[2]},${0.5 * A})`;
            ctx.fillText(s.captured_at.slice(0, 4), ex, ey - 6 * DPR);
            ctx.textAlign = 'left';
        }
    });

    // threads to connected lost things, reaching across the field --- fragments
    // the machine found belong together, drawn as light between them
    for (const rc of (detail.recognitions || [])) {
        const om = markById.get(rc.other_ghost_id);
        if (!om || om._r === -1 || om._sx == null) continue;
        const grad = ctx.createLinearGradient(fm._sx, fm._sy, om._sx, om._sy);
        grad.addColorStop(0, `rgba(${BONE[0]},${BONE[1]},${BONE[2]},${0.22 * A})`);
        grad.addColorStop(1, `rgba(${BONE[0]},${BONE[1]},${BONE[2]},${0.04 * A})`);
        ctx.beginPath();
        const mx = (fm._sx + om._sx) / 2 + (om._sy - fm._sy) * 0.08;   // a gentle bow
        const my = (fm._sy + om._sy) / 2 + (fm._sx - om._sx) * 0.08;
        ctx.moveTo(fm._sx, fm._sy); ctx.quadraticCurveTo(mx, my, om._sx, om._sy);
        ctx.strokeStyle = grad; ctx.lineWidth = 0.8 * DPR;
        ctx.setLineDash([1.5 * DPR, 4 * DPR]); ctx.stroke(); ctx.setLineDash([]);
    }
}

// ---- the instrument readout (real, selectable, copyable text) --------------
const readout = document.getElementById('readout');
const esc = s => ('' + (s == null ? '' : s)).replace(/[&<>"]/g, c =>
    ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));
// follow a connection: clicking a linked thing focuses it and flies there
readout.addEventListener('click', e => {
    const rc = e.target.closest('[data-recompose]');
    if (rc && detail && detail._views && window.openChamber) {
        window.openChamber(detail._views, detail.ghost); return;
    }
    const row = e.target.closest('[data-ghost]');
    if (row) focusGhost(parseInt(row.dataset.ghost, 10));
});

function renderReadout() {
    if (!detail) { readout.innerHTML = ''; readout.classList.remove('shown'); return; }
    const g = detail.ghost, rec = detail.reconstruction;
    const prose = new Set(['body', 'headings', 'outlinks', 'description', 'portrait', 'testimony',
                           'survives_as', 'what_it_was', 'survives_inside']);
    const ticks = n => '<span class="bar">' +
        '▓'.repeat(Math.round((n || 0) * 20)) + '<span class="dim">' +
        '░'.repeat(20 - Math.round((n || 0) * 20)) + '</span></span>';

    let h = `<div class="r-title">${esc(g.label || g.identity)}</div>`;
    h += `<div class="r-id">${esc(g.identity)}</div>`;
    h += `<div class="r-stand">${esc(g.status.toUpperCase().replace('_', ' '))}`;
    if (g.death_kind) h += ` · ${esc(g.death_kind.replace('_', ' '))}`;
    h += ` · worth ${(g.worth || 0).toFixed(2)}</div>`;
    if (g.reference_count > 1)
        h += `<div class="r-recog-row">◈ ${g.reference_count} distinct sites point at this void</div>`;
    if (rec) h += `<div class="r-conf">${ticks(rec.confidence)} ${rec.confidence.toFixed(2)} confidence</div>`;
    if (rec && rec.summary) h += `<div class="r-summary">${esc(rec.summary)}</div>`;
    if ((detail.recognitions || []).length) {
        h += `<div class="r-recog"><span class="r-field">connections — follow a thread</span>`;
        for (const m of detail.recognitions.slice(0, 12)) {
            const tag = (m.kind === 'rebuilt_from') ? '◆ remembers it'
                      : (m.similarity != null ? '≈ ' + Math.round(m.similarity * 100) + '%' : '·');
            h += `<div class="r-recog-row link" data-ghost="${m.other_ghost_id}">` +
                 `${tag}  ${esc((m.label || m.identity || '').slice(0, 44))}</div>`;
        }
        h += `</div>`;
    }

    detail._views = null;
    for (const c of (detail.claims || [])) {
        h += `<div class="r-claim ${c.status}">`;
        h += `<span class="r-field">${esc(c.field.replace(/_/g, ' '))}</span>`;
        h += `<span class="r-cmeta">${c.status} · ${(c.confidence).toFixed(2)}</span>`;
        if (c.field === 'views' && c.value) {
            // recomposed from its surviving images — enter the reconstruction
            let vs = null;
            try { vs = JSON.parse(c.value); } catch (_) { vs = null; }
            if (vs && vs.length) {
                detail._views = vs;
                h += `<div class="r-recompose" data-recompose="1">◉ recomposed from ` +
                     `<span class="n">${vs.length}</span> surviving eyes — enter</div>`;
            }
            h += `<div class="r-why">${esc(c.rationale)}</div>`;
            for (const t of (c.trail || []).slice(0, 2)) {
                const when = t.captured_at ? ' · ' + esc(t.captured_at.slice(0, 10)) : '';
                h += `<div class="r-trail">← ${esc(t.kind)}${when} ${t.uri ? esc(t.uri) : ''}</div>`;
            }
            h += `</div>`;
            continue;
        }
        if (c.field === 'images' && c.value) {
            // the showstopper: actual recovered image bytes, developing out of
            // the dark. real colour --- the one piece of recovered reality.
            h += `<div class="r-gallery">`;
            for (const src of c.value.split('\n').slice(0, 8)) {
                h += `<img class="r-img" loading="lazy" src="${esc(src)}" ` +
                     `onerror="this.remove()" alt="">`;
            }
            h += `</div>`;
        } else if (c.value) {
            const lim = (c.field === 'portrait' || c.field === 'survives_as') ? 1800
                      : (prose.has(c.field) ? 700 : 100000);
            const v = esc(c.value).slice(0, lim);
            h += `<div class="r-val ${prose.has(c.field) ? 'prose' : ''} ${c.field === 'portrait' ? 'r-portrait' : ''}">${v}</div>`;
        }
        h += `<div class="r-why">${esc(c.rationale)}</div>`;
        for (const t of (c.trail || []).slice(0, 4)) {
            const when = t.captured_at ? ' · ' + esc(t.captured_at.slice(0, 10)) : '';
            h += `<div class="r-trail">← ${esc(t.kind)}${when} ${t.uri ? esc(t.uri) : ''}</div>`;
        }
        h += `</div>`;
    }
    readout.innerHTML = h;
    readout.classList.add('shown');
}

// ---- the time scratch (navigate time) -------------------------------------
let axis = { x0: 0, x1: 0, y: 0, hx: 0 };
const ERA_TICKS = [2027, 2020, 2010, 2000, 1990, 1970, 1950, 1900, 1800, 1500, 1000, 1,
                   -500, -1000, -2000];
function drawAxis() {
    const m = 60 * DPR;
    axis.x0 = m; axis.x1 = W - m; axis.y = H - 42 * DPR;
    // the baseline, a long quiet scratch
    ctx.beginPath(); ctx.moveTo(axis.x0, axis.y); ctx.lineTo(axis.x1, axis.y);
    ctx.strokeStyle = `rgba(${ASH[0]},${ASH[1]},${ASH[2]},0.3)`; ctx.lineWidth = DPR; ctx.stroke();
    ctx.textAlign = 'center'; ctx.font = MONO_S;
    let lastx = 1e9;
    for (const yr of ERA_TICKS) {
        if (yr > PRESENT || yr < tMin) continue;
        const x = tToX(yr);
        if (Math.abs(x - lastx) < 34 * DPR) continue;    // avoid label collisions
        lastx = x;
        ctx.beginPath(); ctx.moveTo(x, axis.y - 4 * DPR); ctx.lineTo(x, axis.y + 4 * DPR);
        ctx.strokeStyle = `rgba(${ASH[0]},${ASH[1]},${ASH[2]},0.4)`; ctx.stroke();
        ctx.fillStyle = `rgba(${ASH[0]},${ASH[1]},${ASH[2]},0.5)`; ctx.fillText(fmtYear(yr), x, axis.y + 18 * DPR);
    }
    // marks cast faint ticks onto the timeline where they lived
    for (const mk of marks) {
        const f = markEpoch(mk); if (f == null) continue;
        const x = tToX(Math.max(tMin, Math.min(PRESENT, f)));
        ctx.beginPath(); ctx.moveTo(x, axis.y - 2 * DPR); ctx.lineTo(x, axis.y - 9 * DPR);
        ctx.strokeStyle = `rgba(${BONE[0]},${BONE[1]},${BONE[2]},0.14)`; ctx.stroke();
    }
    // the standpoint: where in time the operator stands
    axis.hx = tToX(tNow);
    ctx.beginPath(); ctx.moveTo(axis.hx, axis.y - 16 * DPR); ctx.lineTo(axis.hx, axis.y + 10 * DPR);
    ctx.strokeStyle = `rgba(${BONE[0]},${BONE[1]},${BONE[2]},0.85)`; ctx.lineWidth = 1.2 * DPR; ctx.stroke();
    ctx.fillStyle = `rgba(${BONE[0]},${BONE[1]},${BONE[2]},0.9)`; ctx.font = MONO;
    ctx.fillText(fmtYear(tNow), axis.hx, axis.y - 22 * DPR);
}

// ---- cursor: the machine's own needle -------------------------------------
function drawCursor() {
    if (pointer.x < -9999) return;
    const x = pointer.x, y = pointer.y, s = 7 * DPR;
    ctx.strokeStyle = `rgba(${BONE[0]},${BONE[1]},${BONE[2]},0.6)`; ctx.lineWidth = DPR;
    ctx.beginPath(); ctx.moveTo(x - s, y); ctx.lineTo(x + s, y);
    ctx.moveTo(x, y - s); ctx.lineTo(x, y + s); ctx.stroke();
}

// ---- frame ----------------------------------------------------------------
function frame(now) {
    revealFrames++;
    // ease the camera toward its target --- the machine flies between connected
    // lost things rather than jumping
    cam.x += (camTarget.x - cam.x) * 0.1;
    cam.y += (camTarget.y - cam.y) * 0.1;
    cam.scale += (camTarget.scale - cam.scale) * 0.1;

    if (soot) ctx.drawImage(soot, 0, 0); else { ctx.fillStyle = SOOT; ctx.fillRect(0, 0, W, H); }
    // a breath of vignette: light falls off toward the edges (physical)
    const v = ctx.createRadialGradient(W / 2, H / 2, Math.min(W, H) * 0.25, W / 2, H / 2, Math.max(W, H) * 0.8);
    v.addColorStop(0, 'rgba(0,0,0,0)'); v.addColorStop(1, 'rgba(0,0,0,0.38)');
    ctx.fillStyle = v; ctx.fillRect(0, 0, W, H);

    hoverId = null;
    let hoverBest = 1e9;
    for (const m of marks) {
        const [sx, sy] = toScreen(m.field_x, m.field_y);
        m._sx = sx; m._sy = sy;
        if (sx < -120 || sx > W + 120 || sy < -120 || sy > H + 120) { m._r = -1; continue; }
        if (!pointer.down) {
            const dx = pointer.x - sx, dy = pointer.y - sy, d2 = dx * dx + dy * dy;
            if (d2 < (36 * DPR) ** 2 && d2 < hoverBest) { hoverBest = d2; hoverId = m.id; }
        }
    }
    for (const m of marks) if (m._r !== -1) scratch(m._sx, m._sy, m, now);

    drawConstellation(now);
    drawHoverLabel();
    drawAxis();
    drawCursor();
    requestAnimationFrame(frame);
}

// the hook: sweep the dark and the name of a lost thing surfaces beside the cursor
function drawHoverLabel() {
    if (hoverId == null) return;
    const m = markById.get(hoverId);
    if (!m || m._sx == null || m._r === -1) return;
    const name = (m.label || m.identity || '').trim();
    if (!name) return;
    const x = m._sx, y = m._sy, r = 13 * DPR;

    // corner brackets: this mark can be opened
    ctx.strokeStyle = `rgba(${BONE[0]},${BONE[1]},${BONE[2]},0.5)`; ctx.lineWidth = DPR;
    for (const [sx, sy] of [[-1, -1], [1, -1], [-1, 1], [1, 1]]) {
        ctx.beginPath();
        ctx.moveTo(x + sx * r, y + sy * r - sy * 5 * DPR);
        ctx.lineTo(x + sx * r, y + sy * r);
        ctx.lineTo(x + sx * r - sx * 5 * DPR, y + sy * r);
        ctx.stroke();
    }

    // the name, whispering out of the soot
    const text = name.length > 58 ? name.slice(0, 57) + '…' : name;
    const kind = m.kind === 'text' ? 'a lost writing' : m.kind === 'place' ? 'a lost place'
               : m.kind === 'lost_work' ? 'a lost work'
               : (m.status === 'at_risk' || m.status === 'preserved') ? 'at risk of being lost'
               : 'a lost page';
    const ep = markEpoch(m);
    const sub = kind + (ep != null ? '   ·   ' + fmtYear(ep) : '');
    ctx.save();
    ctx.textAlign = 'left';
    ctx.shadowColor = 'rgba(6,5,4,0.95)'; ctx.shadowBlur = 11 * DPR;
    ctx.font = `${13 * DPR}px ${SERIF}`;
    ctx.fillStyle = `rgba(${BONE[0]},${BONE[1]},${BONE[2]},0.95)`;
    ctx.fillText(text, x + 18 * DPR, y - 15 * DPR);
    ctx.font = MONO_S;
    ctx.fillStyle = `rgba(${ASH[0]},${ASH[1]},${ASH[2]},0.85)`;
    ctx.fillText(sub, x + 18 * DPR, y - 1 * DPR);
    ctx.restore();
}

// ---- input ----------------------------------------------------------------
function pick(sx, sy) {
    let best = null, bd = 1e9;
    for (const m of marks) {
        if (m._r < 0) continue;
        const d = (m._sx - sx) ** 2 + (m._sy - sy) ** 2;
        if (d < (Math.max(m._r, 22 * DPR)) ** 2 && d < bd) { bd = d; best = m; }
    }
    return best;
}
// Works both ways: a live machine (/api/...) or a frozen static copy the world
// found (field.json / ghosts/<id>.json). Tries the living server first.
async function loadJSON(apiPath, staticPath) {
    try { const r = await fetch(apiPath); if (r.ok) return await r.json(); } catch (e) {}
    try { const r = await fetch(staticPath); if (r.ok) return await r.json(); } catch (e) {}
    return null;
}
async function focusGhost(id) {
    focusId = id;
    detail = await loadJSON('/api/ghost/' + id, 'ghosts/' + id + '.json');
    renderReadout();
    if (detail && detail._views && window.openChamber && /(^|&)rc=[12](&|$)/.test(location.hash))
        window.openChamber(detail._views, detail.ghost);   // deep-link into a reconstruction
    const m = markById.get(id);        // fly the camera to the thing we just focused
    if (m) { camTarget.x = m.field_x; camTarget.y = m.field_y;
             camTarget.scale = Math.max(cam.scale, 2.0); }
    location.hash = 't=' + tNow.toFixed(1) + '&f=' + id;
}
function defocus() { focusId = null; detail = null; renderReadout(); }
canvas.addEventListener('pointerdown', e => {
    pointer.down = true; pointer.mx = e.clientX * DPR; pointer.my = e.clientY * DPR;
    pointer.x = pointer.mx; pointer.y = pointer.my;
    if (Math.abs(pointer.my - axis.y) < 22 * DPR) { pointer.mode = 'time'; onTime(pointer.mx); }
    else pointer.mode = 'pan';
});
addEventListener('pointermove', e => {
    pointer.x = e.clientX * DPR; pointer.y = e.clientY * DPR;
    if (!pointer.down) return;
    if (pointer.mode === 'time') { onTime(pointer.x); return; }
    const e0 = viewExtent() * cam.scale;
    cam.x -= (pointer.x - pointer.mx) / e0; cam.y -= (pointer.y - pointer.my) / e0;
    camTarget.x = cam.x; camTarget.y = cam.y; camTarget.scale = cam.scale;
    pointer.mx = pointer.x; pointer.my = pointer.y;
    pointer._moved = true;
});
addEventListener('pointerup', e => {
    if (pointer.mode === 'pan' && !pointer._moved) {
        const m = pick(e.clientX * DPR, e.clientY * DPR);
        if (m) focusGhost(m.id); else defocus();
    }
    pointer.down = false; pointer.mode = null; pointer._moved = false;
});
canvas.addEventListener('wheel', e => {
    e.preventDefault();
    const [fx, fy] = fromScreen(e.clientX * DPR, e.clientY * DPR);
    cam.scale *= Math.exp(-e.deltaY * 0.0012);
    cam.scale = Math.max(0.25, Math.min(14, cam.scale));
    const [nx, ny] = fromScreen(e.clientX * DPR, e.clientY * DPR);
    cam.x += fx - nx; cam.y += fy - ny;
    camTarget.x = cam.x; camTarget.y = cam.y; camTarget.scale = cam.scale;
}, { passive: false });
function onTime(sx) {
    tNow = Math.max(tMin, Math.min(PRESENT, xToT(Math.max(axis.x0, Math.min(axis.x1, sx)))));
}
addEventListener('pointerleave', () => { pointer.x = -1e4; pointer.y = -1e4; });

// ---- wake -----------------------------------------------------------------
function readHash() {
    // a shareable coordinate in time and attention: #t=2012&f=3
    const h = new URLSearchParams(location.hash.slice(1));
    if (h.has('t')) { const t = parseFloat(h.get('t')); if (!isNaN(t)) tNow = t; }
    if (h.has('f')) { const f = parseInt(h.get('f'), 10); if (!isNaN(f)) focusGhost(f); }
}
async function wake() {
    resize();
    marks = (await loadJSON('/api/field', 'field.json')) || [];
    markById.clear();
    for (const m of marks) markById.set(m.id, m);
    let lo = 3000;
    for (const m of marks) { const y = markEpoch(m); if (y != null) lo = Math.min(lo, y); }
    // reach back to the oldest thing recovered --- antiquity, if a lost text is here
    tMin = Math.floor((lo < 3000 ? lo : 1994) - (lo < 0 ? 150 : 2));
    tMax = PRESENT; tNow = PRESENT;
    readHash();
    addEventListener('hashchange', readHash);
    requestAnimationFrame(frame);
}
wake();
