// ===========================================================================
// THE RECONSTRUCTION CHAMBER --- real 3D, hand-written in raw WebGL.
// No library. No Three.js. No engine. Every matrix, every shader, written here.
//
// A lost thing recomposed from every surviving eye: its surviving photographs
// suspended as planes in a true 3D volume of soot, each at the viewpoint it was
// taken from. Fly in and the thing surrounds you in the dark. Each plate is
// inscribed in the order it was taken --- you watch the thing be seen across the
// years. Light only where evidence survived; everywhere else, soot.
// ===========================================================================
(function () {
'use strict';
const cv = document.getElementById('chamber');
const hud = document.getElementById('chamberhud');
let gl = null, prog = null, loc = {}, quadBuf = null, idxBuf = null, ready = false;

const S = {
    active: false, views: [], title: '', sub: '',
    az: 0.5, el: 0.12, azV: 0, elV: 0,
    rad: 8.2, targetRad: 8.2, dragging: false, lx: 0, ly: 0, moved: 0,
    t: 0, raf: 0, pid: null,
};

// ---- minimal column-major mat4 (no gl-matrix) -----------------------------
function perspective(fovy, aspect, near, far) {
    const f = 1 / Math.tan(fovy / 2), nf = 1 / (near - far);
    return [f / aspect, 0, 0, 0, 0, f, 0, 0,
            0, 0, (far + near) * nf, -1, 0, 0, 2 * far * near * nf, 0];
}
function mul(a, b) {
    const o = new Array(16);
    for (let c = 0; c < 4; c++) for (let r = 0; r < 4; r++) {
        let s = 0;
        for (let k = 0; k < 4; k++) s += a[k * 4 + r] * b[c * 4 + k];
        o[c * 4 + r] = s;
    }
    return o;
}
function lookAt(eye, ctr, up) {
    let z = norm(sub(eye, ctr));
    let x = norm(cross(up, z));
    let y = cross(z, x);
    return [x[0], y[0], z[0], 0, x[1], y[1], z[1], 0, x[2], y[2], z[2], 0,
            -dot(x, eye), -dot(y, eye), -dot(z, eye), 1];
}
const sub = (a, b) => [a[0] - b[0], a[1] - b[1], a[2] - b[2]];
const dot = (a, b) => a[0] * b[0] + a[1] * b[1] + a[2] * b[2];
const cross = (a, b) => [a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0]];
function norm(v) { const l = Math.hypot(v[0], v[1], v[2]) || 1; return [v[0] / l, v[1] / l, v[2] / l]; }

// a plane model matrix: a quad placed at pos, facing outward from the centre,
// scaled to the photo's shape --- a window the thing was seen through.
function planeModel(pos, w, h) {
    const n = norm(pos);
    let r = cross([0, 1, 0], n);
    if (Math.hypot(r[0], r[1], r[2]) < 1e-4) r = [1, 0, 0];
    r = norm(r);
    const u = cross(n, r);
    return [r[0] * w, r[1] * w, r[2] * w, 0,
            u[0] * h, u[1] * h, u[2] * h, 0,
            n[0], n[1], n[2], 0,
            pos[0], pos[1], pos[2], 1];
}

// ---- shaders --------------------------------------------------------------
const VERT = `
attribute vec2 aPos; attribute vec2 aUV;
uniform mat4 uMVP; uniform mat4 uMV;
varying vec2 vUV; varying float vZ;
void main(){
  vUV = aUV;
  vec4 mv = uMV * vec4(aPos, 0.0, 1.0);
  vZ = -mv.z;                    // view-space distance, for fog into soot
  gl_Position = uMVP * vec4(aPos, 0.0, 1.0);
}`;
const FRAG = `
precision mediump float;
uniform sampler2D uTex; uniform float uAlpha; uniform float uMode;
uniform float uFogN; uniform float uFogF;
varying vec2 vUV; varying float vZ;
const vec3 soot = vec3(0.024, 0.020, 0.016);
void main(){
  float fog = clamp((vZ - uFogN) / (uFogF - uFogN), 0.0, 1.0);
  if (uMode > 0.5) {            // scratched frame: white line fading with depth
    gl_FragColor = vec4(mix(vec3(0.95,0.93,0.87), soot, fog*0.85),
                        uAlpha * (1.0 - fog*0.8));
    return;
  }
  vec4 c = texture2D(uTex, vUV);
  float lum = dot(c.rgb, vec3(0.299,0.587,0.114));
  vec3 col = mix(c.rgb, vec3(lum), fog*0.6);         // desaturate into the dark
  col = mix(col, soot, fog*0.8);                     // sink toward soot with depth
  col *= mix(1.1, 0.6, fog);                         // nearer plates brighter
  // a faint vignette on each plate, like a developed negative
  vec2 d = vUV - 0.5;
  col *= 1.0 - dot(d,d)*0.5;
  gl_FragColor = vec4(col, uAlpha * (1.0 - fog*0.35));   // back plates stay ghostly
}`;

function compile(type, src) {
    const s = gl.createShader(type);
    gl.shaderSource(s, src); gl.compileShader(s);
    if (!gl.getShaderParameter(s, gl.COMPILE_STATUS))
        console.warn('shader', gl.getShaderInfoLog(s));
    return s;
}

function initGL() {
    gl = cv.getContext('webgl', { alpha: true, antialias: true, premultipliedAlpha: false });
    if (!gl) { console.warn('no webgl'); return false; }
    prog = gl.createProgram();
    gl.attachShader(prog, compile(gl.VERTEX_SHADER, VERT));
    gl.attachShader(prog, compile(gl.FRAGMENT_SHADER, FRAG));
    gl.linkProgram(prog); gl.useProgram(prog);
    for (const u of ['uMVP', 'uMV', 'uTex', 'uAlpha', 'uMode', 'uFogN', 'uFogF'])
        loc[u] = gl.getUniformLocation(prog, u);
    loc.aPos = gl.getAttribLocation(prog, 'aPos');
    loc.aUV = gl.getAttribLocation(prog, 'aUV');
    // a unit quad: corners + UVs (v flipped so photos stand upright)
    quadBuf = gl.createBuffer();
    gl.bindBuffer(gl.ARRAY_BUFFER, quadBuf);
    gl.bufferData(gl.ARRAY_BUFFER, new Float32Array([
        -0.5, -0.5, 0, 1,  0.5, -0.5, 1, 1,  0.5, 0.5, 1, 0,  -0.5, 0.5, 0, 0]),
        gl.STATIC_DRAW);
    idxBuf = gl.createBuffer();
    gl.bindBuffer(gl.ELEMENT_ARRAY_BUFFER, idxBuf);
    gl.bufferData(gl.ELEMENT_ARRAY_BUFFER, new Uint16Array([0, 1, 2, 2, 3, 0]), gl.STATIC_DRAW);
    gl.enable(gl.BLEND);
    gl.blendFunc(gl.SRC_ALPHA, gl.ONE_MINUS_SRC_ALPHA);
    ready = true;
    return true;
}

function resize() {
    const dpr = Math.min(window.devicePixelRatio || 1, 2);
    cv.width = Math.floor(innerWidth * dpr);
    cv.height = Math.floor(innerHeight * dpr);
    cv.style.width = innerWidth + 'px'; cv.style.height = innerHeight + 'px';
    if (gl) gl.viewport(0, 0, cv.width, cv.height);
}

// a 1x1 soot texture to bind while a plate's real photo is still loading
let sootTex = null;
function makeSootTex() {
    sootTex = gl.createTexture();
    gl.bindTexture(gl.TEXTURE_2D, sootTex);
    gl.texImage2D(gl.TEXTURE_2D, 0, gl.RGBA, 1, 1, 0, gl.RGBA, gl.UNSIGNED_BYTE,
                  new Uint8Array([10, 8, 6, 255]));
}
function loadTex(view) {
    const tex = gl.createTexture();
    gl.bindTexture(gl.TEXTURE_2D, tex);
    gl.texImage2D(gl.TEXTURE_2D, 0, gl.RGBA, 1, 1, 0, gl.RGBA, gl.UNSIGNED_BYTE,
                  new Uint8Array([10, 8, 6, 255]));
    const img = new Image();
    img.crossOrigin = 'anonymous';           // upload.wikimedia.org sends CORS *
    img.onload = () => {
        gl.bindTexture(gl.TEXTURE_2D, tex);
        gl.pixelStorei(gl.UNPACK_FLIP_Y_WEBGL, false);
        try {
            gl.texImage2D(gl.TEXTURE_2D, 0, gl.RGBA, gl.RGBA, gl.UNSIGNED_BYTE, img);
            gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MIN_FILTER, gl.LINEAR);
            gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MAG_FILTER, gl.LINEAR);
            gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_S, gl.CLAMP_TO_EDGE);
            gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_T, gl.CLAMP_TO_EDGE);
            view.aspect = img.naturalWidth / img.naturalHeight || 1;
            view.ready = true;
        } catch (e) { /* tainted or failed: stays soot */ }
    };
    img.src = view.thumb;
    return tex;
}

// fibonacci sphere: scatter the views through a true 3D volume around the thing
function placeViews() {
    const n = S.views.length, R = 4.2, GOLD = Math.PI * (1 + Math.sqrt(5));
    S.views.forEach((v, i) => {
        const k = i + 0.5;
        const phi = Math.acos(1 - 2 * k / n);
        const th = GOLD * k;
        const sp = Math.sin(phi);
        v.pos = [R * sp * Math.cos(th), R * Math.cos(phi) * 0.82, R * sp * Math.sin(th)];
        v.tex = loadTex(v);
        v.aspect = v.aspect || 1.4;
        v.reveal = i;                         // inscribed in order of date (sorted below)
    });
}

function year(d) { const m = ('' + (d || '')).match(/(-?\d{3,4})/); return m ? +m[1] : null; }

function openChamber(viewsData, ghost) {
    if (!gl && !initGL()) return;
    if (!sootTex) makeSootTex();
    resize();
    // order by the year each was taken, so the thing is inscribed through time
    S.views = viewsData.map(v => ({ ...v, y: year(v.date) }))
        .sort((a, b) => (a.y == null) - (b.y == null) || (a.y || 0) - (b.y || 0));
    placeViews();
    S.title = (ghost && (ghost.label || ghost.identity)) || '';
    const ys = S.views.map(v => v.y).filter(x => x != null);
    const span = ys.length ? (Math.min(...ys) + (ys.length > 1 ? '–' + Math.max(...ys) : '')) : '';
    S.sub = `${S.views.length} surviving eyes${span ? '   ·   ' + span : ''}`;
    S.az = 0.5; S.el = 0.12; S.rad = 9.5; S.targetRad = 7; S.t = 0;
    S.active = true;
    cv.classList.add('active'); hud.classList.add('active');
    hud.innerHTML = `<div class="ch-title">${escapeHTML(S.title)}</div>` +
        `<div class="ch-sub">${escapeHTML(S.sub)}</div>` +
        `<div class="ch-hint">drag to orbit&nbsp;&nbsp;·&nbsp;&nbsp;scroll to fly in&nbsp;&nbsp;·&nbsp;&nbsp;esc to close</div>`;
    cancelAnimationFrame(S.raf);
    S.raf = requestAnimationFrame(frame);
}
function closeChamber() {
    S.active = false;
    cv.classList.remove('active'); hud.classList.remove('active');
    cancelAnimationFrame(S.raf);
}
function escapeHTML(s) {
    return ('' + (s == null ? '' : s)).replace(/[&<>"]/g, c =>
        ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));
}

function frame() {
    if (!S.active) return;
    S.t += 0.016;
    if (!S.dragging) { S.az += 0.0013; S.az += S.azV; S.azV *= 0.93; S.elV *= 0.93; S.el += S.elV; }
    S.el = Math.max(-1.2, Math.min(1.2, S.el));
    S.rad += (S.targetRad - S.rad) * 0.08;

    const asp = cv.width / cv.height;
    const proj = perspective(55 * Math.PI / 180, asp, 0.1, 80);
    const ce = Math.cos(S.el), se = Math.sin(S.el);
    const eye = [S.rad * ce * Math.cos(S.az), S.rad * se, S.rad * ce * Math.sin(S.az)];
    const view = lookAt(eye, [0, 0, 0], [0, 1, 0]);
    const vp = mul(proj, view);

    gl.viewport(0, 0, cv.width, cv.height);
    gl.clearColor(0.024, 0.020, 0.016, 1);
    gl.clear(gl.COLOR_BUFFER_BIT);
    gl.useProgram(prog);
    gl.uniform1f(loc.uFogN, 3.0);            // the cloud stays legible; depth only dims
    gl.uniform1f(loc.uFogF, 18.0);

    gl.bindBuffer(gl.ARRAY_BUFFER, quadBuf);
    gl.enableVertexAttribArray(loc.aPos);
    gl.vertexAttribPointer(loc.aPos, 2, gl.FLOAT, false, 16, 0);
    gl.enableVertexAttribArray(loc.aUV);
    gl.vertexAttribPointer(loc.aUV, 2, gl.FLOAT, false, 16, 8);
    gl.bindBuffer(gl.ELEMENT_ARRAY_BUFFER, idxBuf);
    gl.activeTexture(gl.TEXTURE0);
    gl.uniform1i(loc.uTex, 0);

    // sort back-to-front for correct blending
    const ord = S.views.map((v, i) => {
        const dz = sub(v.pos, eye);
        return { i, d: dot(dz, dz) };
    }).sort((a, b) => b.d - a.d);

    for (const o of ord) {
        const v = S.views[o.i];
        const rv = Math.max(0, Math.min(1, (S.t * 9 - v.reveal * 0.6)));   // inscribe through time
        if (rv <= 0) continue;
        const h = 1.9, w = h * (v.aspect || 1.4);
        const model = planeModel(v.pos, w, h);
        gl.uniformMatrix4fv(loc.uMVP, false, mul(vp, model));
        gl.uniformMatrix4fv(loc.uMV, false, mul(view, model));
        // photo
        gl.bindTexture(gl.TEXTURE_2D, v.ready ? v.tex : sootTex);
        gl.uniform1f(loc.uMode, 0);
        gl.uniform1f(loc.uAlpha, 0.96 * rv);
        gl.drawElements(gl.TRIANGLES, 6, gl.UNSIGNED_SHORT, 0);
        // scratched frame
        gl.uniform1f(loc.uMode, 1);
        gl.uniform1f(loc.uAlpha, 0.5 * rv);
        gl.drawArrays(gl.LINE_LOOP, 0, 4);
    }
    S.raf = requestAnimationFrame(frame);
}

// ---- interaction ----------------------------------------------------------
cv.addEventListener('pointerdown', e => {
    S.dragging = true; S.moved = 0; S.lx = e.clientX; S.ly = e.clientY;
    S.pid = e.pointerId; cv.setPointerCapture(e.pointerId); cv.classList.add('dragging');
});
cv.addEventListener('pointermove', e => {
    if (!S.dragging) return;
    const dx = e.clientX - S.lx, dy = e.clientY - S.ly;
    S.lx = e.clientX; S.ly = e.clientY; S.moved += Math.abs(dx) + Math.abs(dy);
    S.az -= dx * 0.006; S.el += dy * 0.006; S.azV = -dx * 0.0006; S.elV = dy * 0.0006;
});
cv.addEventListener('pointerup', e => {
    S.dragging = false; cv.classList.remove('dragging');
    if (S.moved < 6) closeChamber();            // a click, not a drag: leave
});
cv.addEventListener('wheel', e => {
    e.preventDefault();
    S.targetRad = Math.max(1.2, Math.min(16, S.targetRad + e.deltaY * 0.004));
}, { passive: false });
addEventListener('keydown', e => { if (e.key === 'Escape' && S.active) closeChamber(); });
addEventListener('resize', () => { if (S.active) resize(); });

window.openChamber = openChamber;
window.closeChamber = closeChamber;
})();
