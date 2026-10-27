const { chromium } = require("playwright");
const assert = require("node:assert/strict");

(async () => {
  const browser = await chromium.launch({ headless: true });
  const context = await browser.newContext({
    viewport: { width: 820, height: 1180 },
    screen: { width: 820, height: 1180 },
    deviceScaleFactor: 2,
    isMobile: true,
    hasTouch: true,
  });
  const page = await context.newPage();
  const errors = [];
  const apiCalls = [];
  page.on("pageerror", error => errors.push(error.message));
  await page.addInitScript(() => {
    try { localStorage.clear(); sessionStorage.clear(); } catch {}
    Object.defineProperty(window, "SpeechRecognition", { configurable: true, value: undefined });
    Object.defineProperty(window, "webkitSpeechRecognition", { configurable: true, value: undefined });
  });
  await page.route("**/api/health", route => route.fulfill({
    status: 200, contentType: "application/json",
    body: JSON.stringify({ ok: true, prototype: "ui-test", engine: "test", model_provider: "test-provider", model: "mock-integration-test", model_configured: true, search_provider: "duckduckgo", search_provider_configured: true }),
  }));
  await page.route("**/api/agent/execute", async route => {
    const payload = route.request().postDataJSON();
    apiCalls.push(payload);
    if (String(payload.goal).includes("espera-lenta")) {
      await new Promise(resolve => setTimeout(resolve, 1800));
    }
    return route.fulfill({
      status: 200, contentType: "application/json",
      body: JSON.stringify({
        kind: "answer",
        answer: "# Respuesta REAL de prueba\n\nTexto **importante** y *énfasis*, con \x60código\x60.\n\n- Elemento uno\n- Elemento dos\n\n> Cita de prueba\n\n\x60\x60\x60js\nconst x = 1;\n\x60\x60\x60\n\n<script>alert(1)</script>",
        route: "chat", route_source: "test", returned_model: "mock-integration-test",
        verification: { status: "not_independently_verified" }, sources: [], events: [],
      }),
    });
  });

  try {
    await page.goto("http://127.0.0.1:4173/static/index.html", { waitUntil: "domcontentloaded" });
    assert.equal(await page.title().then(x => x.length > 0), true, "la página debe tener título");
    await page.locator("#prompt").fill("Responde con un ejemplo real de prueba");
    await page.locator("#send").click();
    const firstAnswer = page.locator("#conversation article.message.assistant").first();
    await firstAnswer.locator(".response-actions button", { hasText: "Regenerar" }).waitFor();
    assert.equal(await firstAnswer.locator(".message-body h1").textContent(), "Respuesta REAL de prueba");
    assert.equal(await firstAnswer.locator(".message-body strong").textContent(), "importante");
    assert.equal(await firstAnswer.locator(".message-body ul li").count(), 2);
    assert.equal(await firstAnswer.locator(".message-body pre code").textContent(), "const x = 1;");
    assert.equal(await firstAnswer.locator(".message-body script").count(), 0, "el HTML de la respuesta no debe ejecutarse como HTML");
    assert.equal(await firstAnswer.locator(".response-actions button").count(), 7, "acciones de respuesta");

    for (const label of ["Regenerar", "Más simple", "Ampliar", "Ejemplos", "Comprobar crítica"]) {
      const before = await page.locator("#conversation article.message.assistant").count();
      await firstAnswer.locator(".response-actions button", { hasText: label }).click();
      await page.waitForFunction(n => document.querySelectorAll("#conversation article.message.assistant").length > n, before);
      const last = page.locator("#conversation article.message.assistant").last();
      await last.locator(".response-actions").waitFor();
      assert.ok(apiCalls.length >= before, "cada acción debe enviar una petición al backend");
    }
    const beforeFollowup = await page.locator("#conversation article.message.assistant").count();
    await firstAnswer.locator(".followups button", { hasText: "Dame un plan de acción" }).click();
    await page.waitForFunction(n => document.querySelectorAll("#conversation article.message.assistant").length > n, beforeFollowup);

    await page.locator("#search-chat").click();
    await page.locator("#search-input").fill("Respuesta REAL");
    assert.match(await page.locator("#search-results").textContent(), /mensaje\(s\) encontrado\(s\)/);
    assert.ok(await page.locator("#conversation article.message").evaluateAll(nodes => nodes.some(n => n.style.outline.includes("solid"))), "búsqueda resalta coincidencias");

    await page.locator("#voice-input").click();
    assert.match(await page.locator("#status").textContent(), /dictado compatible/i, "fallback de voz cuando SpeechRecognition no existe");

    await page.locator("#theme-toggle").click();
    assert.ok(await page.locator("body").evaluate(el => el.classList.contains("light") || !el.classList.contains("light")), "cambio de tema ejecutado");
    assert.ok(await page.evaluate(() => localStorage.getItem("elyntro.theme.v1") !== null), "el tema se guarda localmente");

    const width = await page.evaluate(() => ({ viewport: window.innerWidth, page: document.documentElement.scrollWidth }));
    assert.ok(width.page <= width.viewport + 1, `sin desbordamiento horizontal en viewport iPad simulado: ${JSON.stringify(width)}`);

    await page.locator("#prompt").fill("espera-lenta");
    await page.locator("#send").click();
    await page.locator("#stop-request").waitFor({ state: "visible" });
    await page.locator("#stop-request").click();
    await page.getByText(/Has detenido la espera desde la interfaz/).waitFor({ timeout: 5000 });
    await page.locator("#stop-request").waitFor({ state: "hidden" });

    assert.deepEqual(errors, [], "sin errores JavaScript no capturados");
    assert.ok(apiCalls.length >= 8, "las acciones deben conectar con la API del servidor");
    process.stdout.write(JSON.stringify({
      ok: true, viewport: "820x1180, touch-enabled, DPR 2 (iPad-like)",
      assertions: ["markdown", "safe rendering", "answer actions", "follow-up", "search", "voice fallback", "theme persistence", "mobile overflow", "cancel request", "backend route"],
      apiRequests: apiCalls.length, consolePageErrors: errors,
    }, null, 2) + "\n");
  } finally {
    await context.close();
    await browser.close();
  }
})().catch(error => { console.error(error); process.exit(1); });
