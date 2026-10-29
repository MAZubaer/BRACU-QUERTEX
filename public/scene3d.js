// 3D background: the 199 corpus documents orbit a glowing "knowledge core".
// It follows the chat: searching speeds the orbit up, the passages sent to the
// AI fly forward in their status colour (the same colours as the badges), the
// core pulses while the answer streams in, and when it is done only the passages
// the answer cited stay out, matching the Sources list on the page.
// The chat works without it (no WebGL, CDN blocked, etc.).
//
// API for the page:  window.scene3d.setState('idle'|'search'|'answer'|'error'),
//                    .showSources([status, ...]), .keepSources([cited S-numbers]),
//                    .pulse(), .setChatting(bool), .reset()
import * as THREE from 'https://cdn.jsdelivr.net/npm/three@0.160.0/build/three.module.js';

// documents in the corpus by status (BRACU_RAG_Corpus/MANIFEST.csv, duplicates removed)
const CORPUS = { current: 188, draft_not_approved: 6, current_partially_superseded: 3,
                 reference_undated: 1, superseded: 1 };
const MAX_SLOTS = 8;                       // top-k (7) + the rules guide
const COLUMN_PX = 852;                     // chat column width in index.html (820 + gutters)
const SIDE_MIN_WIDTH = 1180;               // wide enough to show the library beside the chat
const FRONT_Z = 3.5;                       // how far the answer's sources come forward

const COLORS = {
  dark: {
    fog: 0x070b14, core: 0x3b82f6, coreBody: 0x0a1630, shell: 0x8fb8ff, ring: 0x7c6cff,
    dust: 0x9cc3ff, paper: 0xc9d8f2,
    status: { current: 0x4ade80, current_partially_superseded: 0xfbbf24, draft_not_approved: 0xfb923c,
              reference_undated: 0xa3b1c6, superseded: 0xf87171 },
    tint: { current: 0xc9d8f2, current_partially_superseded: 0xf3dca0, draft_not_approved: 0xf5c29a,
            reference_undated: 0xb9c3d3, superseded: 0xf1a5a5 },
    error: 0xef4444,
  },
  light: {
    fog: 0xeef3fb, core: 0x2563eb, coreBody: 0xdbe7ff, shell: 0x2f5fbf, ring: 0x6d28d9,
    dust: 0x2f5fbf, paper: 0xffffff,
    status: { current: 0x15803d, current_partially_superseded: 0x92600a, draft_not_approved: 0xb24a06,
              reference_undated: 0x556070, superseded: 0xc4232d },     // = the badge colours in index.html
    tint: { current: 0xf8fbff, current_partially_superseded: 0xfdf0cf, draft_not_approved: 0xfde3cf,
            reference_undated: 0xe2e8f0, superseded: 0xfbd5d5 },
    error: 0xdc2626,
  },
};

function webglAvailable() {
  try {
    const c = document.createElement('canvas');
    return !!(window.WebGLRenderingContext && (c.getContext('webgl2') || c.getContext('webgl')));
  } catch { return false; }
}

// small deterministic random, so the library looks the same on every visit
function rng(seed) {
  return () => { seed = (seed * 1664525 + 1013904223) >>> 0; return seed / 4294967296; };
}

function glowTexture() {
  const c = document.createElement('canvas');
  c.width = c.height = 128;
  const g = c.getContext('2d');
  const grad = g.createRadialGradient(64, 64, 0, 64, 64, 64);
  grad.addColorStop(0, 'rgba(255,255,255,1)');
  grad.addColorStop(0.25, 'rgba(255,255,255,.45)');
  grad.addColorStop(1, 'rgba(255,255,255,0)');
  g.fillStyle = grad;
  g.fillRect(0, 0, 128, 128);
  return new THREE.CanvasTexture(c);
}

function start(canvas) {
  const reduceQ = matchMedia('(prefers-reduced-motion: reduce)');
  const darkQ = matchMedia('(prefers-color-scheme: dark)');
  // the page's theme switch sets <html data-theme>; without it, follow the device
  const isDark = () => (document.documentElement.dataset.theme || (darkQ.matches ? 'dark' : 'light')) === 'dark';
  const isSmall = () => innerWidth < 700;

  const renderer = new THREE.WebGLRenderer({ canvas, antialias: true, alpha: true,
                                             powerPreference: 'low-power' });
  renderer.setPixelRatio(Math.min(devicePixelRatio || 1, 1.75));
  const scene = new THREE.Scene();
  const camera = new THREE.PerspectiveCamera(42, 1, 0.1, 100);
  camera.position.set(0, 0.4, 16);

  const world = new THREE.Group();
  world.rotation.set(0.24, 0, -0.1);
  scene.add(world);

  // ---- lights
  const ambient = new THREE.AmbientLight(0xffffff, 0.55);
  const key = new THREE.DirectionalLight(0xffffff, 0.9);
  key.position.set(4, 6, 8);
  const coreLight = new THREE.PointLight(0x3b82f6, 30, 14, 1.6);
  scene.add(ambient, key);
  world.add(coreLight);

  // ---- the core
  const core = new THREE.Group();
  world.add(core);
  const coreMat = new THREE.MeshStandardMaterial({ roughness: 0.25, metalness: 0.3, emissiveIntensity: 1.1 });
  const coreMesh = new THREE.Mesh(new THREE.IcosahedronGeometry(1.0, 4), coreMat);
  const shellMat = new THREE.LineBasicMaterial({ transparent: true, opacity: 0.55 });
  const shell = new THREE.LineSegments(new THREE.EdgesGeometry(new THREE.IcosahedronGeometry(1.5, 1)), shellMat);
  const glowMat = new THREE.SpriteMaterial({ map: glowTexture(), transparent: true, depthWrite: false,
                                             blending: THREE.AdditiveBlending, opacity: 0.85 });
  const glow = new THREE.Sprite(glowMat);
  glow.scale.setScalar(6.5);
  const ringMat = new THREE.MeshBasicMaterial({ transparent: true, opacity: 0.5 });
  const rings = [2.2, 2.75].map((r, i) => {
    const m = new THREE.Mesh(new THREE.TorusGeometry(r, 0.012, 8, 180), ringMat);
    m.rotation.set(Math.PI / 2 + (i ? 0.5 : -0.3), i ? 0.4 : 0, 0);
    return m;
  });
  core.add(glow, coreMesh, shell, ...rings);

  // ---- the documents (one instanced mesh, 199 thin cards)
  const statuses = [];
  for (const [s, n] of Object.entries(CORPUS)) for (let i = 0; i < n; i++) statuses.push(s);
  const rand = rng(7);
  for (let i = statuses.length - 1; i > 0; i--) {            // shuffle
    const j = Math.floor(rand() * (i + 1));
    [statuses[i], statuses[j]] = [statuses[j], statuses[i]];
  }
  const N = statuses.length;
  const docs = new THREE.InstancedMesh(
    new THREE.BoxGeometry(0.4, 0.54, 0.014),
    new THREE.MeshStandardMaterial({ roughness: 0.5, metalness: 0.05, transparent: true, opacity: 0.86 }),
    N);
  const docMat = docs.material;
  docs.instanceMatrix.setUsage(THREE.DynamicDrawUsage);
  world.add(docs);
  // the answer's sources are drawn unlit in a second mesh, so their colour is
  // exactly the status colour (no lighting, no blue glow mixed in)
  const srcCards = new THREE.InstancedMesh(docs.geometry,
    new THREE.MeshBasicMaterial({ fog: false, toneMapped: false }), MAX_SLOTS);
  srcCards.instanceMatrix.setUsage(THREE.DynamicDrawUsage);
  srcCards.setColorAt(0, new THREE.Color());
  srcCards.count = 0;
  world.add(srcCards);
  const SWAP = 0.3;                          // highlight level at which a card switches meshes

  const doc = statuses.map((status) => {
    const r = 3.0 + rand() * 3.0;
    return {
      status, r,
      a: rand() * Math.PI * 2,
      y: (rand() - 0.5) * (0.5 + r * 0.16),
      speed: (0.05 + rand() * 0.07) * (4.2 / r),
      bob: rand() * Math.PI * 2,
      spin: (rand() - 0.5) * 0.9,
      h: 0, ht: 0,                           // highlight (0..1) and its target
      n: 0, ring: 0, rings: 1, ang: 0,       // source number [Sn], place in the ring, current angle
    };
  });

  // ---- dust
  const dustCount = isSmall() ? 320 : 700;
  const dustPos = new Float32Array(dustCount * 3);
  for (let i = 0; i < dustCount; i++) {
    const r = 7 + rand() * 11, t = rand() * Math.PI * 2, p = Math.acos(2 * rand() - 1);
    dustPos.set([r * Math.sin(p) * Math.cos(t), r * Math.cos(p) * 0.6, r * Math.sin(p) * Math.sin(t)], i * 3);
  }
  const dustGeo = new THREE.BufferGeometry();
  dustGeo.setAttribute('position', new THREE.BufferAttribute(dustPos, 3));
  const dustMat = new THREE.PointsMaterial({ size: 0.05, transparent: true, depthWrite: false });
  const dust = new THREE.Points(dustGeo, dustMat);
  scene.add(dust);

  // ---- theme
  let C = COLORS.dark;
  const tmpColor = new THREE.Color(), tintColor = new THREE.Color(), hiColor = new THREE.Color();
  function applyTheme() {
    const dark = isDark();
    C = dark ? COLORS.dark : COLORS.light;
    scene.fog = new THREE.Fog(C.fog, 13, 30);
    coreMat.color.set(C.coreBody);
    coreMat.emissive.set(C.core);
    shellMat.color.set(C.shell);
    ringMat.color.set(C.ring);
    glowMat.color.set(C.core);
    glowMat.opacity = dark ? 0.85 : 0.45;
    coreLight.color.set(C.core);
    dustMat.color.set(C.dust);
    dustMat.opacity = dark ? 0.7 : 0.35;
    dustMat.blending = dark ? THREE.AdditiveBlending : THREE.NormalBlending;
    dustMat.needsUpdate = true;
    ambient.intensity = dark ? 0.5 : 0.9;
    docMat.emissive.set(dark ? 0x1e3a8a : 0x000000);       // cards seen from behind are not flat grey
    docMat.emissiveIntensity = dark ? 0.55 : 0;
    requestRender();
  }

  // ---- state driven by the chat
  const state = { name: 'idle', speed: 1, speedT: 1, pulse: 0, error: 0, chatting: 0, chattingT: 0 };
  const SPEED = { idle: 1, search: 6, answer: 1.8, error: 0.5 };

  function setState(name) {
    state.name = name;
    state.speedT = SPEED[name] ?? 1;
    if (name === 'search') release();
    if (name === 'error') { state.error = 1; release(); }
    requestRender();
  }
  function release() { doc.forEach((d) => { d.ht = 0; }); }
  // every passage sent to the AI comes forward ...
  function showSources(list) {
    release();
    const used = new Set();
    const shown = (list || []).slice(0, MAX_SLOTS);
    shown.forEach((status, j) => {
      let i = doc.findIndex((d, k) => !used.has(k) && d.status === status);
      if (i < 0) i = doc.findIndex((d, k) => !used.has(k) && d.status === 'current');
      if (i < 0) return;
      used.add(i);
      Object.assign(doc[i], { ht: 1, n: j + 1, ring: j, rings: shown.length });
    });
    requestRender();
  }
  // ... and once the answer is done, only the cited ones stay (like the page's
  // Sources list; if nothing was cited, the page lists them all, and so do we)
  function keepSources(cited) {
    const keep = new Set(cited || []);
    const shown = doc.filter((d) => d.ht > 0);
    const kept = shown.filter((d) => keep.has(d.n)).sort((a, b) => a.n - b.n);
    if (!kept.length) return;
    shown.forEach((d) => { if (!keep.has(d.n)) d.ht = 0; });
    kept.forEach((d, j) => { d.ring = j; d.rings = kept.length; });
    requestRender();
  }
  function pulse() { state.pulse = Math.min(1, state.pulse + 0.18); }
  function setChatting(on) { state.chattingT = on ? 1 : 0; requestRender(); }
  function reset() { release(); setState('idle'); setChatting(false); }

  // ---- pointer parallax
  const pointer = { x: 0, y: 0, tx: 0, ty: 0 };
  addEventListener('pointermove', (e) => {
    pointer.tx = e.clientX / innerWidth - 0.5;
    pointer.ty = e.clientY / innerHeight - 0.5;
  }, { passive: true });

  // ---- layout
  function resize() {
    const w = innerWidth, h = innerHeight;
    renderer.setSize(w, h, false);
    camera.aspect = w / h;
    camera.fov = w < 700 ? 58 : 42;
    camera.updateProjectionMatrix();
    requestRender();
  }
  addEventListener('resize', resize);

  // ---- per-frame update
  const dummy = new THREE.Object3D();
  const qOrbit = new THREE.Quaternion(), qFront = new THREE.Quaternion(), qWorldInv = new THREE.Quaternion();
  const pOrbit = new THREE.Vector3(), pFront = new THREE.Vector3(), lookTarget = new THREE.Vector3();
  const ease = (t) => t * t * (3 - 2 * t);
  let time = 0;

  function update(dt, snap) {
    time += dt;
    const k = (rate) => (snap ? 1 : 1 - Math.exp(-rate * dt));
    state.speed += (state.speedT - state.speed) * k(2.5);
    state.chatting += (state.chattingT - state.chatting) * k(2);
    state.pulse *= snap ? 0 : Math.exp(-3 * dt);
    state.error *= snap ? 0 : Math.exp(-1.5 * dt);
    pointer.x += (pointer.tx - pointer.x) * k(3);
    pointer.y += (pointer.ty - pointer.y) * k(3);

    // camera: the core sits at ~28% of the screen height, above the welcome text.
    // While chatting it moves into the right-hand margin on wide screens (so the
    // sources flying forward are not hidden behind the answer), else to the centre.
    const small = isSmall();
    const dist = small ? 18 : 16;
    const halfH = Math.tan(THREE.MathUtils.degToRad(camera.fov / 2));
    const lift = -0.44 * dist * halfH * (1 - state.chatting);
    const side = innerWidth >= SIDE_MIN_WIDTH ? state.chatting : 0;
    camera.position.x = pointer.x * 1.4;
    camera.position.y = 0.4 - pointer.y * 0.9;
    camera.position.z = dist + state.chatting * 2;
    lookTarget.set(0, lift, 0);
    camera.lookAt(lookTarget);
    const column = Math.min(innerWidth, COLUMN_PX);
    const marginCentre = (innerWidth + column) / 4 / innerWidth;   // right margin, as a fraction from the centre
    world.position.set(marginCentre * 2 * (dist + 2) * halfH * camera.aspect * side, -0.2 * side, 0);
    world.scale.setScalar((small ? 0.78 : 1) * (1 - 0.5 * side));

    // core
    const searching = state.name === 'search' ? 1 : 0;
    const beat = 1 + state.pulse * 0.12 + searching * 0.05 * Math.sin(time * 9);
    coreMesh.scale.setScalar(beat);
    coreMesh.rotation.y += dt * (0.25 + state.speed * 0.08);
    shell.rotation.y -= dt * (0.15 + state.speed * 0.12);
    shell.rotation.x += dt * 0.05;
    rings[0].rotation.z += dt * 0.2 * state.speed;
    rings[1].rotation.z -= dt * 0.14 * state.speed;
    tmpColor.set(C.core).lerp(tintColor.set(C.error), state.error);
    coreMat.emissive.copy(tmpColor);
    glowMat.color.copy(tmpColor);
    coreMat.emissiveIntensity = 1 + state.pulse * 1.4 + searching * 0.4;
    coreLight.intensity = 26 + state.pulse * 40 + searching * 18;
    glow.scale.setScalar(6.2 + state.pulse * 1.6 + searching * 0.8);
    dust.rotation.y += dt * 0.012 * state.speed;

    // documents
    world.updateMatrixWorld();
    qWorldInv.copy(world.quaternion).invert();
    let nSrc = 0;
    for (let i = 0; i < N; i++) {
      const d = doc[i];
      d.a += dt * d.speed * state.speed;
      d.h += (d.ht - d.h) * k(d.ht > d.h ? 2.4 : 1.8);
      const e = ease(Math.min(1, Math.max(0, d.h)));

      const swirl = searching * 0.55;                       // pulled inward while searching
      const r = d.r * (1 - swirl * 0.35);
      pOrbit.set(Math.cos(d.a) * r, d.y * (1 - swirl * 0.5) + Math.sin(time * 0.8 + d.bob) * 0.12,
                 Math.sin(d.a) * r);
      dummy.position.copy(pOrbit);
      dummy.lookAt(0, pOrbit.y, 0);
      dummy.rotateZ(d.spin + time * 0.1 * d.spin);
      qOrbit.copy(dummy.quaternion);

      // ring angle glides to its place (the ring closes up when uncited ones leave)
      const angT = -Math.PI / 2 + (d.ring / d.rings) * Math.PI * 2;
      if (d.h < 0.05) d.ang = angT;
      else d.ang += Math.atan2(Math.sin(angT - d.ang), Math.cos(angT - d.ang)) * k(4);

      // core beside the chat: sources come forward in a ring around it (scaled for
      // depth so they stay on screen). Otherwise the ring would be hidden behind
      // the answer, so they only light up in place.
      if (e > 0.001 && side > 0.5) {
        const kz = (camera.position.z - FRONT_Z) / camera.position.z;
        pFront.set((world.position.x + Math.cos(d.ang) * 1.7) * kz,
                   (world.position.y + Math.sin(d.ang) * 1.9) * kz, FRONT_Z);
        world.worldToLocal(pFront);
        dummy.position.lerpVectors(pOrbit, pFront, e);
        qFront.copy(qWorldInv);
        dummy.quaternion.slerpQuaternions(qOrbit, qFront, e);
      }
      dummy.scale.setScalar(1 + e * (side > 0.5 ? 0.9 : 0.5));
      dummy.updateMatrix();

      if (e > SWAP && nSrc < MAX_SLOTS) {
        // a source: drawn in its exact status colour; hidden in the orbit mesh
        srcCards.setMatrixAt(nSrc, dummy.matrix);
        srcCards.setColorAt(nSrc, tmpColor.set(C.status[d.status] ?? C.paper));
        nSrc++;
        dummy.scale.setScalar(0);
        dummy.updateMatrix();
      }
      docs.setMatrixAt(i, dummy.matrix);
      tintColor.set(C.tint[d.status] ?? C.paper);
      hiColor.set(C.status[d.status] ?? C.paper);
      docs.setColorAt(i, tmpColor.copy(tintColor).lerp(hiColor, d.status === 'current' ? 0 : 0.35));
    }
    docs.instanceMatrix.needsUpdate = true;
    docs.instanceColor.needsUpdate = true;
    srcCards.count = nSrc;
    srcCards.instanceMatrix.needsUpdate = true;
    srcCards.instanceColor.needsUpdate = true;
  }

  // ---- render loop (only while visible; still frames when motion is reduced)
  let last = performance.now(), running = false, pending = false;
  function frame(now) {
    const dt = Math.min(0.05, (now - last) / 1000);
    last = now;
    update(dt, false);
    renderer.render(scene, camera);
    if (running) requestAnimationFrame(frame);
  }
  function requestRender() {
    if (running || pending) return;
    pending = true;
    requestAnimationFrame(() => {
      pending = false;
      update(0.016, true);
      renderer.render(scene, camera);
    });
  }
  function setRunning() {
    const want = !reduceQ.matches && !document.hidden;
    if (want && !running) { running = true; last = performance.now(); requestAnimationFrame(frame); }
    else if (!want) { running = false; requestRender(); }
  }
  document.addEventListener('visibilitychange', setRunning);
  reduceQ.addEventListener?.('change', setRunning);
  darkQ.addEventListener?.('change', applyTheme);
  new MutationObserver(applyTheme).observe(document.documentElement, { attributes: true, attributeFilter: ['data-theme'] });

  applyTheme();
  resize();
  setRunning();
  document.documentElement.classList.add('has-3d');
  window.scene3d = { setState, showSources, keepSources, pulse, setChatting, reset };
  window.dispatchEvent(new Event('scene3d-ready'));
}

const canvas = document.getElementById('scene');
if (canvas && webglAvailable()) {
  try { start(canvas); } catch (e) {
    console.warn('3D scene off:', e);
    document.documentElement.classList.add('no-3d');
  }
} else {
  document.documentElement.classList.add('no-3d');
}
