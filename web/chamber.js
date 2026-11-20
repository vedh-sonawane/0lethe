// ===========================================================================
// THE WORLD --- a real, accurate 3D world you walk, built from real map data.
// Raw WebGL, hand-written: no Three.js, no engine, no library.
//
// For any lost thing that had a place, the machine bakes the real geometry of
// that exact spot from OpenStreetMap --- actual building footprints and heights,
// streets, water --- and this stands it up as a solid, sun-lit world you explore
// on foot, with gravity. The lost thing itself rises where it really stood, a
// luminous wireframe wearing the photographs that survived it. Everything solid
// is real survey; nothing is invented.
// ===========================================================================
(function () {
'use strict';
const cv = document.getElementById('chamber');
const hud = document.getElementById('chamberhud');
let gl = null, world = null, lprog = null, pprog = null, lloc = {}, ploc = {}, wloc = {};
let ground = null, bld = null, lines = null, ghost = null, photos = [];
let quadBuf = null, idxBuf = null, sootTex = null, ready = false;

const EYE = 1.7, GRAV = 0.025, JUMP = 0.42;
const S = {
    active: false, title: '', sub: '',
    pos: [0, EYE, 36], yaw: Math.PI, pitch: -0.02, vel: [0, 0, 0], vy: 0, grounded: true,
    keys: Object.create(null), dragging: false, lx: 0, ly: 0, moved: 0, t: 0, raf: 0,
    foot: [],                                   // building footprints, for collision
};

// ---- mat4 (column-major) --------------------------------------------------
function perspective(f, a, n, fr) { const t = 1 / Math.tan(f / 2), d = 1 / (n - fr);
    return [t / a, 0, 0, 0, 0, t, 0, 0, 0, 0, (fr + n) * d, -1, 0, 0, 2 * fr * n * d, 0]; }
function mul(a, b) { const o = new Array(16);
    for (let c = 0; c < 4; c++) for (let r = 0; r < 4; r++) { let s = 0;
        for (let k = 0; k < 4; k++) s += a[k * 4 + r] * b[c * 4 + k]; o[c * 4 + r] = s; } return o; }
function lookAt(e, c, up) { let z = nrm(sub(e, c)), x = nrm(crs(up, z)), y = crs(z, x);
    return [x[0], y[0], z[0], 0, x[1], y[1], z[1], 0, x[2], y[2], z[2], 0,
            -dot(x, e), -dot(y, e), -dot(z, e), 1]; }
const sub = (a, b) => [a[0] - b[0], a[1] - b[1], a[2] - b[2]];
const dot = (a, b) => a[0] * b[0] + a[1] * b[1] + a[2] * b[2];
const crs = (a, b) => [a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0]];
function nrm(v) { const l = Math.hypot(v[0], v[1], v[2]) || 1; return [v[0] / l, v[1] / l, v[2] / l]; }

// ---- shaders --------------------------------------------------------------
const V_WORLD = `attribute vec3 aPos; attribute vec3 aNrm; attribute vec2 aUV;
uniform mat4 uVP; varying vec3 vN; varying float vZ; varying vec2 vUV;
void main(){ gl_Position = uVP*vec4(aPos,1.0); vN=aNrm; vZ=gl_Position.w; vUV=aUV; }`;
const F_WORLD = `precision mediump float;
uniform vec3 uSun; uniform vec3 uBase; uniform float uFogN,uFogF; uniform float uTextured;
uniform sampler2D uTex;
varying vec3 vN; varying float vZ; varying vec2 vUV;
const vec3 haze=vec3(0.44,0.42,0.39);
void main(){
  vec3 n=normalize(vN);
  float l=max(dot(n,uSun),0.0);
  float sky=clamp(n.y*0.5+0.5,0.0,1.0);
  float lum = 0.34 + 1.15*l + 0.26*sky;                    // strong sun: lit vs shadowed sides read
  vec3 col;
  if(uTextured>0.5 && vUV.x>=0.0){ col=texture2D(uTex,vUV).rgb*lum; }   // facade: windows + wall
  else if(uTextured>0.5){ col=vec3(0.17,0.16,0.14)*lum; }              // roof
  else { col=uBase*lum; }                                               // ground base
  float fog=clamp((vZ-uFogN)/(uFogF-uFogN),0.0,1.0);
  gl_FragColor=vec4(mix(col,haze,fog),1.0);
}`;
const V_LINE = `attribute vec3 aPos; uniform mat4 uVP; varying float vZ;
void main(){ gl_Position=uVP*vec4(aPos,1.0); vZ=gl_Position.w; }`;
const F_LINE = `precision mediump float; uniform float uFogN,uFogF,uAlpha; uniform vec3 uCol;
varying float vZ; const vec3 soot=vec3(0.44,0.42,0.39);
void main(){ float fog=clamp((vZ-uFogN)/(uFogF-uFogN),0.0,1.0);
  gl_FragColor=vec4(mix(uCol,soot,fog), uAlpha*(1.0-fog)); }`;
const V_PH = `attribute vec2 aPos; attribute vec2 aUV; uniform mat4 uMVP,uMV;
varying vec2 vUV; varying float vZ;
void main(){ vUV=aUV; vZ=-(uMV*vec4(aPos,0.0,1.0)).z; gl_Position=uMVP*vec4(aPos,0.0,1.0); }`;
const F_PH = `precision mediump float; uniform sampler2D uTex; uniform float uAlpha,uMode,uFogN,uFogF;
varying vec2 vUV; varying float vZ; const vec3 soot=vec3(0.44,0.42,0.39);
void main(){ float fog=clamp((vZ-uFogN)/(uFogF-uFogN),0.0,1.0);
  if(uMode>0.5){ gl_FragColor=vec4(mix(vec3(0.95,0.93,0.86),soot,fog*0.8),uAlpha*(1.0-fog*0.8)); return; }
  vec4 c=texture2D(uTex,vUV); vec3 col=mix(c.rgb,soot,fog*0.85);
  gl_FragColor=vec4(col, uAlpha*(1.0-fog*0.5)); }`;

// real satellite/aerial ground --- free, keyless Esri World Imagery, draped as
// textured tiles at true metre scale. this is the real surface of the planet.
const V_TG = `attribute vec2 aXZ; attribute vec2 aUV; uniform mat4 uVP;
uniform float uOffX,uOffZ,uSize; varying vec2 vUV; varying float vZ;
void main(){ vec4 w=vec4(aXZ.x*uSize+uOffX, 0.03, aXZ.y*uSize+uOffZ, 1.0);
  gl_Position=uVP*w; vUV=aUV; vZ=gl_Position.w; }`;
const F_TG = `precision mediump float; uniform sampler2D uTex; uniform float uFogN,uFogF;
varying vec2 vUV; varying float vZ; const vec3 soot=vec3(0.44,0.42,0.39);
void main(){ vec4 c=texture2D(uTex,vUV); float fog=clamp((vZ-uFogN)/(uFogF-uFogN),0.0,1.0);
  gl_FragColor=vec4(mix(c.rgb*0.95,soot,fog),1.0); }`;
let tgprog = null, tgloc = {}, tgQuad = null, sat = [];

// a real sky all around --- a dome that moves with you, so there is never a void:
// warm haze at the horizon rising to a deep dusk overhead.
const V_SKY = `attribute vec3 aPos; uniform mat4 uVP; uniform vec3 uCam; uniform float uR;
varying float vY; void main(){ gl_Position=uVP*vec4(aPos*uR+uCam,1.0); vY=aPos.y; }`;
const F_SKY = `precision mediump float; uniform vec3 uZen,uHaze; varying float vY;
void main(){ float t=clamp(vY,0.0,1.0); gl_FragColor=vec4(mix(uHaze,uZen,pow(t,0.62)),1.0); }`;
let skyprog = null, skyloc = {}, skyBuf = null, skyN = 0;
const HAZE = [0.44, 0.42, 0.39], ZEN = [0.10, 0.12, 0.18];

let facadeTex = null;
// one window bay drawn to a canvas, tiled across every façade so buildings read
// as real buildings --- rows of windows and floors --- not grey blocks. The only
// thing generated; the shapes, heights and positions are all real map survey.
function makeFacade() {
    const c = document.createElement('canvas'); c.width = c.height = 64;
    const x = c.getContext('2d');
    x.fillStyle = '#9a9184'; x.fillRect(0, 0, 64, 64);                               // wall
    x.fillStyle = '#3c434d'; x.fillRect(11, 9, 42, 46);                               // glass
    const g = x.createLinearGradient(0, 9, 0, 55); g.addColorStop(0, '#5a636f'); g.addColorStop(1, '#333a43');
    x.fillStyle = g; x.fillRect(11, 9, 42, 46);
    x.strokeStyle = '#726856'; x.lineWidth = 3; x.strokeRect(11, 9, 42, 46);          // frame
    x.fillStyle = '#726856'; x.fillRect(31, 9, 2, 46); x.fillRect(11, 31, 42, 2);     // mullions
    const t = gl.createTexture(); gl.bindTexture(gl.TEXTURE_2D, t);
    gl.texImage2D(gl.TEXTURE_2D, 0, gl.RGBA, gl.RGBA, gl.UNSIGNED_BYTE, c);
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_S, gl.REPEAT);
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_T, gl.REPEAT);
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MIN_FILTER, gl.LINEAR_MIPMAP_LINEAR);
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MAG_FILTER, gl.LINEAR);
    gl.generateMipmap(gl.TEXTURE_2D);
    return t;
}

function sh(t, s) { const o = gl.createShader(t); gl.shaderSource(o, s); gl.compileShader(o);
    if (!gl.getShaderParameter(o, gl.COMPILE_STATUS)) console.warn(gl.getShaderInfoLog(o)); return o; }
function program(vs, fs) { const p = gl.createProgram();
    gl.attachShader(p, sh(gl.VERTEX_SHADER, vs)); gl.attachShader(p, sh(gl.FRAGMENT_SHADER, fs));
    gl.linkProgram(p); return p; }

function initGL() {
    gl = cv.getContext('webgl', { alpha: true, antialias: true, depth: true });
    if (!gl) return false;
    wloc.p = program(V_WORLD, F_WORLD);
    for (const u of ['uVP', 'uSun', 'uBase', 'uFogN', 'uFogF', 'uTextured', 'uTex']) wloc[u] = gl.getUniformLocation(wloc.p, u);
    wloc.aPos = gl.getAttribLocation(wloc.p, 'aPos'); wloc.aNrm = gl.getAttribLocation(wloc.p, 'aNrm');
    wloc.aUV = gl.getAttribLocation(wloc.p, 'aUV');
    facadeTex = makeFacade();
    lprog = program(V_LINE, F_LINE);
    for (const u of ['uVP', 'uFogN', 'uFogF', 'uAlpha', 'uCol']) lloc[u] = gl.getUniformLocation(lprog, u);
    lloc.aPos = gl.getAttribLocation(lprog, 'aPos');
    pprog = program(V_PH, F_PH);
    for (const u of ['uMVP', 'uMV', 'uTex', 'uAlpha', 'uMode', 'uFogN', 'uFogF']) ploc[u] = gl.getUniformLocation(pprog, u);
    ploc.aPos = gl.getAttribLocation(pprog, 'aPos'); ploc.aUV = gl.getAttribLocation(pprog, 'aUV');
    quadBuf = gl.createBuffer(); gl.bindBuffer(gl.ARRAY_BUFFER, quadBuf);
    gl.bufferData(gl.ARRAY_BUFFER, new Float32Array([-0.5, -0.5, 0, 1, 0.5, -0.5, 1, 1, 0.5, 0.5, 1, 0, -0.5, 0.5, 0, 0]), gl.STATIC_DRAW);
    idxBuf = gl.createBuffer(); gl.bindBuffer(gl.ELEMENT_ARRAY_BUFFER, idxBuf);
    gl.bufferData(gl.ELEMENT_ARRAY_BUFFER, new Uint16Array([0, 1, 2, 2, 3, 0]), gl.STATIC_DRAW);
    skyprog = program(V_SKY, F_SKY);
    for (const u of ['uVP', 'uCam', 'uR', 'uZen', 'uHaze']) skyloc[u] = gl.getUniformLocation(skyprog, u);
    skyloc.aPos = gl.getAttribLocation(skyprog, 'aPos');
    { const V = [], rings = 16, segs = 24, pt = (a, b) => [Math.sin(a) * Math.cos(b), Math.cos(a), Math.sin(a) * Math.sin(b)];
      for (let i = 0; i < rings; i++) for (let j = 0; j < segs; j++) {
        const a0 = i / rings * Math.PI, a1 = (i + 1) / rings * Math.PI, b0 = j / segs * 2 * Math.PI, b1 = (j + 1) / segs * 2 * Math.PI;
        const A = pt(a0, b0), B = pt(a1, b0), C = pt(a1, b1), D = pt(a0, b1);
        V.push(...A, ...B, ...C, ...A, ...C, ...D); }
      skyN = V.length / 3; skyBuf = gl.createBuffer(); gl.bindBuffer(gl.ARRAY_BUFFER, skyBuf);
      gl.bufferData(gl.ARRAY_BUFFER, new Float32Array(V), gl.STATIC_DRAW); }
    tgprog = program(V_TG, F_TG);
    for (const u of ['uVP', 'uOffX', 'uOffZ', 'uSize', 'uTex', 'uFogN', 'uFogF']) tgloc[u] = gl.getUniformLocation(tgprog, u);
    tgloc.aXZ = gl.getAttribLocation(tgprog, 'aXZ'); tgloc.aUV = gl.getAttribLocation(tgprog, 'aUV');
    tgQuad = gl.createBuffer(); gl.bindBuffer(gl.ARRAY_BUFFER, tgQuad);
    gl.bufferData(gl.ARRAY_BUFFER, new Float32Array([-0.5, -0.5, 0, 0, 0.5, -0.5, 1, 0, 0.5, 0.5, 1, 1, -0.5, 0.5, 0, 1]), gl.STATIC_DRAW);
    sootTex = gl.createTexture(); gl.bindTexture(gl.TEXTURE_2D, sootTex);
    gl.texImage2D(gl.TEXTURE_2D, 0, gl.RGBA, 1, 1, 0, gl.RGBA, gl.UNSIGNED_BYTE, new Uint8Array([12, 10, 8, 255]));
    gl.enable(gl.DEPTH_TEST); gl.enable(gl.BLEND); gl.blendFunc(gl.SRC_ALPHA, gl.ONE_MINUS_SRC_ALPHA);
    ready = true; return true;
}

function resize() {
    const dpr = Math.min(window.devicePixelRatio || 1, 2);
    cv.width = Math.floor(innerWidth * dpr); cv.height = Math.floor(innerHeight * dpr);
    cv.style.width = innerWidth + 'px'; cv.style.height = innerHeight + 'px';
    if (gl) gl.viewport(0, 0, cv.width, cv.height);
}

// ---- build the world's geometry from the baked map ------------------------
function buf(arr) { const b = gl.createBuffer(); gl.bindBuffer(gl.ARRAY_BUFFER, b);
    gl.bufferData(gl.ARRAY_BUFFER, new Float32Array(arr), gl.STATIC_DRAW); return b; }

function extrude(poly, h, V) {       // walls (windowed) + a centroid-fan roof, pos+nrm+uv
    const n = poly.length, floors = Math.max(1, h / 3.3);
    let cx = 0, cz = 0; for (const p of poly) { cx += p[0]; cz += p[1]; } cx /= n; cz /= n;
    for (let i = 0; i < n; i++) {
        const a = poly[i], b = poly[(i + 1) % n];
        const ex = b[0] - a[0], ez = b[1] - a[1];
        const len = Math.hypot(ex, ez) || 1;
        let nx = ez / len, nz = -ex / len;
        const bays = Math.max(1, Math.round(len / 4));         // ~4m per window bay
        // wall quad a0-b0-bH-aH, uv tiles by bays across and floors up
        const w = [[a[0], 0, a[1], 0, 0], [b[0], 0, b[1], bays, 0],
                   [b[0], h, b[1], bays, floors], [a[0], h, a[1], 0, floors]];
        for (const t of [[0, 1, 2], [0, 2, 3]]) for (const k of t) {
            V.push(w[k][0], w[k][1], w[k][2], nx, 0, nz, w[k][3], w[k][4]);
        }
        // roof triangle (uv.x=-1 marks it as roof, not façade)
        V.push(cx, h, cz, 0, 1, 0, -1, -1, a[0], h, a[1], 0, 1, 0, -1, -1, b[0], h, b[1], 0, 1, 0, -1, -1);
    }
}

function buildWorld(w) {
    S.foot = []; S.meCentroid = [0, 0]; S.meH = 10; ghost = null;
    // ground: a big plane out to the horizon, beneath the imagery
    const R = Math.max((w.radius || 320) * 1.6, 4000);
    ground = buf([-R, 0, -R, 0, 1, 0, -1, -1, R, 0, -R, 0, 1, 0, -1, -1, R, 0, R, 0, 1, 0, -1, -1,
                  -R, 0, -R, 0, 1, 0, -1, -1, R, 0, R, 0, 1, 0, -1, -1, -R, 0, R, 0, 1, 0, -1, -1]);
    // solid buildings (everything but the lost thing) merged into one mesh
    const V = [];
    const edges = [];                            // the lost building, as a wireframe ghost
    for (const b of (w.buildings || [])) {
        const h = b.h > 0 ? b.h : 8.5;
        if (b.me) {
            for (let i = 0; i < b.p.length; i++) {
                const a = b.p[i], c = b.p[(i + 1) % b.p.length];
                edges.push(a[0], 0, a[1], a[0], h, a[1]);        // vertical
                edges.push(a[0], h, a[1], c[0], h, c[1]);        // roofline
                edges.push(a[0], 0, a[1], c[0], 0, c[1]);        // base
            }
            S.meCentroid = b.p.reduce((s, p) => [s[0] + p[0], s[1] + p[1]], [0, 0]).map(x => x / b.p.length);
            S.meH = h;
        } else {
            extrude(b.p, h, V);
            S.foot.push(b.p);
        }
    }
    bld = { b: buf(V), n: V.length / 8 };
    ghost = edges.length ? { b: buf(edges), n: edges.length / 3 } : null;
    // roads as faint ground lines
    const L = [];
    for (const r of (w.roads || [])) for (let i = 0; i < r.length - 1; i++)
        L.push(r[i][0], 0.1, r[i][1], r[i + 1][0], 0.1, r[i + 1][1]);
    lines = L.length ? { b: buf(L), n: L.length / 3 } : null;
}

// the real ground: fetch a grid of Esri World Imagery tiles around the place
// (free, keyless, CORS-open) and lay them at true metre scale. This is the
// actual surface of the earth there --- real, not invented.
function loadSatellite(lat, lon) {
    sat = [];
    if (lat == null || lon == null) return;
    const z = 17, n = Math.pow(2, z), R = 4;
    const cxf = (lon + 180) / 360 * n;
    const rl = lat * Math.PI / 180;
    const cyf = (1 - Math.log(Math.tan(rl) + 1 / Math.cos(rl)) / Math.PI) / 2 * n;
    const Tm = 2 * Math.PI * 6378137 * Math.cos(rl) / n;       // tile edge in metres
    const ctx = Math.floor(cxf), cty = Math.floor(cyf);
    for (let tx = ctx - R; tx <= ctx + R; tx++) for (let ty = cty - R; ty <= cty + R; ty++) {
        const t = { offX: (tx + 0.5 - cxf) * Tm, offZ: (ty + 0.5 - cyf) * Tm, size: Tm,
                    tex: gl.createTexture(), ready: false };
        gl.bindTexture(gl.TEXTURE_2D, t.tex);
        gl.texImage2D(gl.TEXTURE_2D, 0, gl.RGBA, 1, 1, 0, gl.RGBA, gl.UNSIGNED_BYTE, new Uint8Array([20, 22, 20, 255]));
        const img = new Image(); img.crossOrigin = 'anonymous';
        img.onload = (tt => () => { gl.bindTexture(gl.TEXTURE_2D, tt.tex);
            try { gl.texImage2D(gl.TEXTURE_2D, 0, gl.RGBA, gl.RGBA, gl.UNSIGNED_BYTE, img);
                gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MIN_FILTER, gl.LINEAR);
                gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MAG_FILTER, gl.LINEAR);
                gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_S, gl.CLAMP_TO_EDGE);
                gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_T, gl.CLAMP_TO_EDGE);
                tt.ready = true; } catch (e) {} })(t);
        img.src = `https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/${z}/${ty}/${tx}`;
        sat.push(t);
    }
}

// (kept for other modes; not used in the world)
function placePhotos(views) {
    const c = S.meCentroid || [0, 0], h = S.meH || 10;
    photos = views.map((v, i) => {
        const o = { ...v, ready: false, tex: null };
        const ang = i / Math.max(1, views.length) * Math.PI * 2;
        const rad = 10 + (i % 3) * 3;
        o.h = Math.min(h, 8); o.w = o.h * (v.aspect || 1.4);
        o.pos = [c[0] + Math.cos(ang) * rad, o.h / 2, c[1] + Math.sin(ang) * rad];
        o.face = Math.atan2(c[0] - o.pos[0], c[1] - o.pos[2]);
        o.tex = loadTex(o);
        return o;
    });
}
function loadTex(view) {
    const tex = gl.createTexture(); gl.bindTexture(gl.TEXTURE_2D, tex);
    gl.texImage2D(gl.TEXTURE_2D, 0, gl.RGBA, 1, 1, 0, gl.RGBA, gl.UNSIGNED_BYTE, new Uint8Array([12, 10, 8, 255]));
    const img = new Image(); img.crossOrigin = 'anonymous';
    img.onload = () => { gl.bindTexture(gl.TEXTURE_2D, tex);
        try { gl.texImage2D(gl.TEXTURE_2D, 0, gl.RGBA, gl.RGBA, gl.UNSIGNED_BYTE, img);
            gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MIN_FILTER, gl.LINEAR);
            gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_S, gl.CLAMP_TO_EDGE);
            gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_T, gl.CLAMP_TO_EDGE);
            view.aspect = img.naturalWidth / img.naturalHeight || view.aspect; view.ready = true;
        } catch (e) {} };
    img.src = view.thumb; return tex;
}

// ---- open / close ---------------------------------------------------------
function openChamber(payload, ghostRec) {
    if (!gl && !initGL()) return;
    resize();
    world = payload.world || { buildings: [], roads: [], water: [], radius: 160 };
    buildWorld(world);
    photos = [];
    const ctr = world.center || [null, null];
    loadSatellite(ctr[0], ctr[1]);               // the real ground, from orbit
    S.title = (ghostRec && (ghostRec.label || ghostRec.identity)) || '';
    const nb = (world.buildings || []).length;
    S.sub = `standing where it stood · ${nb} real buildings · real aerial ground`;
    const c = S.meCentroid || [0, 0];
    // arrive low over the city, looking across it, then settle to the street to walk
    S.pos = [c[0], 52, c[1] + 105]; S.yaw = Math.PI; S.pitch = -0.33; S.vy = 0; S.t = 0;
    if (/(^|&)rc=3(&|$)/.test(location.hash)) { S.pos = [c[0], 220, c[1] + 220]; S.pitch = -0.6; }
    S.keys = Object.create(null); S.vel = [0, 0, 0];
    S.active = true; window.__chamberActive = true;
    cv.classList.add('active'); hud.classList.add('active'); updateHud();
    cancelAnimationFrame(S.raf); S.raf = requestAnimationFrame(frame);
}
function closeChamber() { S.active = false; window.__chamberActive = false;
    cv.classList.remove('active'); hud.classList.remove('active'); cancelAnimationFrame(S.raf); }
function escapeHTML(s) { return ('' + (s == null ? '' : s)).replace(/[&<>"]/g, c =>
    ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c])); }
function updateHud() {
    hud.innerHTML = `<div class="ch-title">${escapeHTML(S.title)}</div>` +
        `<div class="ch-sub">${escapeHTML(S.sub)}</div>` +
        `<div class="ch-hint">W A S D to walk · drag to look · hold space to rise & fly · esc to leave</div>`;
}
function lookDir() { const cp = Math.cos(S.pitch); return [cp * Math.sin(S.yaw), Math.sin(S.pitch), cp * Math.cos(S.yaw)]; }

function inBuilding(x, z) {
    for (const poly of S.foot) {
        let inside = false;
        for (let i = 0, j = poly.length - 1; i < poly.length; j = i++) {
            const xi = poly[i][0], zi = poly[i][1], xj = poly[j][0], zj = poly[j][1];
            if (((zi > z) !== (zj > z)) && (x < (xj - xi) * (z - zi) / (zj - zi) + xi)) inside = !inside;
        }
        if (inside) return true;
    }
    return false;
}

function frame() {
    if (!S.active) return;
    S.t += 0.016;
    const asp = cv.width / cv.height;
    const proj = perspective(60 * Math.PI / 180, asp, 0.5, 9000);

    // WALK the streets: heading from yaw (looking up never lifts your feet), with
    // gravity and building collision. Hold space to rise and fly for the overview,
    // let go and you fall back to the ground --- walk down there, fly up here.
    const fwd = [Math.sin(S.yaw), 0, Math.cos(S.yaw)], rgt = [Math.cos(S.yaw), 0, -Math.sin(S.yaw)];
    const flying = S.pos[1] > EYE + 0.5;
    const a = flying ? 0.004 * S.pos[1] : 0.05, k = S.keys, acc = [0, 0];
    if (k['w'] || k['arrowup']) { acc[0] += fwd[0] * a; acc[1] += fwd[2] * a; }
    if (k['s'] || k['arrowdown']) { acc[0] -= fwd[0] * a; acc[1] -= fwd[2] * a; }
    if (k['d'] || k['arrowright']) { acc[0] += rgt[0] * a; acc[1] += rgt[2] * a; }
    if (k['a'] || k['arrowleft']) { acc[0] -= rgt[0] * a; acc[1] -= rgt[2] * a; }
    S.vel = [(S.vel[0] + acc[0]) * 0.82, 0, (S.vel[2] + acc[1]) * 0.82];
    let nx = S.pos[0] + S.vel[0], nz = S.pos[2] + S.vel[2];
    if (flying || !inBuilding(nx, S.pos[2])) S.pos[0] = nx; else S.vel[0] = 0;
    if (flying || !inBuilding(S.pos[0], nz)) S.pos[2] = nz; else S.vel[2] = 0;
    // vertical: space rises, shift sinks, gravity pulls back to the street
    if (k[' '] || k['e']) S.vy += 0.045;
    else if (k['shift'] || k['q'] || k['c']) S.vy -= 0.06;
    else S.vy -= GRAV;
    S.pos[1] += S.vy; S.vy *= 0.86;
    if (S.pos[1] <= EYE) { S.pos[1] = EYE; S.vy = 0; }
    if (S.pos[1] > 600) { S.pos[1] = 600; S.vy = 0; }
    const f = lookDir();
    const view = lookAt(S.pos, [S.pos[0] + f[0], S.pos[1] + f[1], S.pos[2] + f[2]], [0, 1, 0]);
    const vp = mul(proj, view); S.vp = vp;
    const sun = nrm([0.5, 0.55, 0.35]);

    gl.viewport(0, 0, cv.width, cv.height);
    gl.clearColor(HAZE[0], HAZE[1], HAZE[2], 1); gl.clear(gl.COLOR_BUFFER_BIT | gl.DEPTH_BUFFER_BIT);
    const fogN = 160, fogF = 2600;

    // the sky, all around you --- a dome riding with the camera. no void, ever.
    gl.useProgram(skyprog); gl.depthMask(false);
    gl.uniformMatrix4fv(skyloc.uVP, false, vp); gl.uniform3fv(skyloc.uCam, S.pos);
    gl.uniform1f(skyloc.uR, 6000); gl.uniform3fv(skyloc.uZen, ZEN); gl.uniform3fv(skyloc.uHaze, HAZE);
    gl.bindBuffer(gl.ARRAY_BUFFER, skyBuf);
    gl.enableVertexAttribArray(skyloc.aPos); gl.vertexAttribPointer(skyloc.aPos, 3, gl.FLOAT, false, 0, 0);
    gl.drawArrays(gl.TRIANGLES, 0, skyN);
    gl.depthMask(true);

    // ground + buildings, lit
    gl.useProgram(wloc.p);
    gl.uniformMatrix4fv(wloc.uVP, false, vp);
    gl.uniform3fv(wloc.uSun, sun); gl.uniform1f(wloc.uFogN, fogN); gl.uniform1f(wloc.uFogF, fogF);
    gl.activeTexture(gl.TEXTURE0); gl.bindTexture(gl.TEXTURE_2D, facadeTex); gl.uniform1i(wloc.uTex, 0);
    const drawWorld = (b, n, base, textured) => {
        gl.uniform3fv(wloc.uBase, base); gl.uniform1f(wloc.uTextured, textured ? 1 : 0);
        gl.bindBuffer(gl.ARRAY_BUFFER, b);
        gl.enableVertexAttribArray(wloc.aPos); gl.vertexAttribPointer(wloc.aPos, 3, gl.FLOAT, false, 32, 0);
        gl.enableVertexAttribArray(wloc.aNrm); gl.vertexAttribPointer(wloc.aNrm, 3, gl.FLOAT, false, 32, 12);
        gl.enableVertexAttribArray(wloc.aUV); gl.vertexAttribPointer(wloc.aUV, 2, gl.FLOAT, false, 32, 24);
        gl.drawArrays(gl.TRIANGLES, 0, n);
    };
    drawWorld(ground, 6, [0.06, 0.056, 0.045], 0);       // dark base beneath the imagery
    // the real aerial ground, tile by tile
    if (sat.length) {
        gl.useProgram(tgprog); gl.uniformMatrix4fv(tgloc.uVP, false, vp);
        gl.uniform1f(tgloc.uFogN, fogN); gl.uniform1f(tgloc.uFogF, fogF);
        gl.bindBuffer(gl.ARRAY_BUFFER, tgQuad);
        gl.enableVertexAttribArray(tgloc.aXZ); gl.vertexAttribPointer(tgloc.aXZ, 2, gl.FLOAT, false, 16, 0);
        gl.enableVertexAttribArray(tgloc.aUV); gl.vertexAttribPointer(tgloc.aUV, 2, gl.FLOAT, false, 16, 8);
        gl.bindBuffer(gl.ELEMENT_ARRAY_BUFFER, idxBuf); gl.activeTexture(gl.TEXTURE0); gl.uniform1i(tgloc.uTex, 0);
        for (const t of sat) { gl.uniform1f(tgloc.uOffX, t.offX); gl.uniform1f(tgloc.uOffZ, t.offZ);
            gl.uniform1f(tgloc.uSize, t.size); gl.bindTexture(gl.TEXTURE_2D, t.ready ? t.tex : sootTex);
            gl.drawElements(gl.TRIANGLES, 6, gl.UNSIGNED_SHORT, 0); }
    }
    if (bld) drawWorld(bld.b, bld.n, [1, 1, 1], 1);      // real buildings, windowed façades

    // roads
    if (lines) { gl.useProgram(lprog); gl.uniformMatrix4fv(lloc.uVP, false, vp);
        gl.uniform1f(lloc.uFogN, fogN); gl.uniform1f(lloc.uFogF, fogF);
        gl.uniform1f(lloc.uAlpha, 0.22); gl.uniform3f(lloc.uCol, 0.33, 0.31, 0.27);
        gl.bindBuffer(gl.ARRAY_BUFFER, lines.b);
        gl.enableVertexAttribArray(lloc.aPos); gl.vertexAttribPointer(lloc.aPos, 3, gl.FLOAT, false, 0, 0);
        gl.drawArrays(gl.LINES, 0, lines.n); }

    // the lost thing: a luminous wireframe where it really stood
    if (ghost) { gl.useProgram(lprog); gl.uniformMatrix4fv(lloc.uVP, false, vp);
        gl.uniform1f(lloc.uFogN, fogN); gl.uniform1f(lloc.uFogF, fogF);
        gl.uniform1f(lloc.uAlpha, 0.9); gl.uniform3f(lloc.uCol, 0.95, 0.92, 0.84);
        gl.bindBuffer(gl.ARRAY_BUFFER, ghost.b);
        gl.enableVertexAttribArray(lloc.aPos); gl.vertexAttribPointer(lloc.aPos, 3, gl.FLOAT, false, 0, 0);
        gl.drawArrays(gl.LINES, 0, ghost.n); }

    // the surviving photographs, standing at the site
    if (photos.length) {
        gl.useProgram(pprog); gl.uniform1f(ploc.uFogN, fogN); gl.uniform1f(ploc.uFogF, fogF);
        gl.bindBuffer(gl.ARRAY_BUFFER, quadBuf);
        gl.enableVertexAttribArray(ploc.aPos); gl.vertexAttribPointer(ploc.aPos, 2, gl.FLOAT, false, 16, 0);
        gl.enableVertexAttribArray(ploc.aUV); gl.vertexAttribPointer(ploc.aUV, 2, gl.FLOAT, false, 16, 8);
        gl.bindBuffer(gl.ELEMENT_ARRAY_BUFFER, idxBuf); gl.activeTexture(gl.TEXTURE0); gl.uniform1i(ploc.uTex, 0);
        for (const v of photos) {
            const s = Math.sin(v.face), co = Math.cos(v.face);
            const model = [co * v.w, 0, -s * v.w, 0, 0, v.h, 0, 0, s, 0, co, 0, v.pos[0], v.pos[1], v.pos[2], 1];
            gl.uniformMatrix4fv(ploc.uMVP, false, mul(vp, model));
            gl.uniformMatrix4fv(ploc.uMV, false, mul(view, model));
            gl.bindTexture(gl.TEXTURE_2D, v.ready ? v.tex : sootTex);
            gl.uniform1f(ploc.uMode, 0); gl.uniform1f(ploc.uAlpha, 0.98);
            gl.drawElements(gl.TRIANGLES, 6, gl.UNSIGNED_SHORT, 0);
            gl.uniform1f(ploc.uMode, 1); gl.uniform1f(ploc.uAlpha, 0.55);
            gl.drawArrays(gl.LINE_LOOP, 0, 4);
        }
    }
    S.raf = requestAnimationFrame(frame);
}

// ---- controls -------------------------------------------------------------
cv.addEventListener('pointerdown', e => { S.dragging = true; S.moved = 0; S.lx = e.clientX; S.ly = e.clientY;
    cv.setPointerCapture(e.pointerId); cv.classList.add('dragging'); });
cv.addEventListener('pointermove', e => { if (!S.dragging) return;
    const dx = e.clientX - S.lx, dy = e.clientY - S.ly; S.lx = e.clientX; S.ly = e.clientY;
    S.moved += Math.abs(dx) + Math.abs(dy);
    S.yaw += dx * 0.004; S.pitch = Math.max(-1.4, Math.min(1.4, S.pitch - dy * 0.004)); });
cv.addEventListener('pointerup', e => { S.dragging = false; cv.classList.remove('dragging');
    if (S.moved < 6) closeChamber(); });
addEventListener('keydown', e => { if (!S.active) return;
    if (e.key === 'Escape') { closeChamber(); return; }
    const k = e.key.toLowerCase();
    if ('wasdeqc '.includes(k) || k === 'shift' || k.startsWith('arrow')) { S.keys[k] = true; e.preventDefault(); } });
addEventListener('keyup', e => { if (S.active) S.keys[e.key.toLowerCase()] = false; });
addEventListener('resize', () => { if (S.active) resize(); });

window.openChamber = openChamber;
window.closeChamber = closeChamber;
})();
