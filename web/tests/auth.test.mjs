import { describe, test } from 'node:test'
import assert from 'node:assert/strict'
import { api, getApiToken, setApiToken, validateAndSaveApiToken } from '../src/api/client.ts'

const TOKEN_KEY = 'icle.apiToken'
const OLD_TOKEN = 'synthetic-existing-token'
const NEW_TOKEN = 'synthetic-replacement-token'

// All credentials, storage and HTTP responses in this file are synthetic.
function browser({ seed = true } = {}) {
  const values = new Map()
  const events = []
  const timers = new Map()
  let timerId = 0
  const storage = {
    getItem: (key) => values.get(key) ?? null,
    setItem: (key, value) => values.set(key, value),
    removeItem: (key) => values.delete(key),
  }
  globalThis.window = {
    localStorage: storage,
    dispatchEvent: (event) => { events.push(event.type); return true },
    setTimeout: (callback) => { timers.set(++timerId, callback); return timerId },
    clearTimeout: (id) => timers.delete(id),
  }
  globalThis.fetch = async () => { throw new Error('Unexpected HTTP request in test') }
  if (seed) setApiToken(OLD_TOKEN)
  return { values, storage, events, timers }
}

function statusResponse() {
  return Response.json({ state: 'idle', active: [], provider: { configured: false } })
}

describe('API token authentication', { concurrency: false }, () => {
  test('initial storage reads tolerate blocked storage, trim pasted line endings and reject corrupt values', () => {
    const { storage, values } = browser({ seed: false })
    const read = storage.getItem
    storage.getItem = () => { throw new DOMException('Access denied', 'SecurityError') }
    assert.equal(getApiToken(), '')
    storage.getItem = read
    values.set(TOKEN_KEY, ` \r\n${OLD_TOKEN}\r\n `)
    assert.equal(getApiToken(), OLD_TOKEN)
    values.set(TOKEN_KEY, 'corrupt\u0000token')
    assert.equal(getApiToken(), '')
    values.delete(TOKEN_KEY)
    assert.equal(getApiToken(), '')
  })

  test('empty and malformed candidates do not send requests or replace a working credential', async () => {
    const { values, events } = browser()
    const candidates = [
      [' \r\n ', 'empty'],
      ['first\nsecond', 'format'],
      ['token\u0000', 'format'],
      ['错误口令', 'format'],
    ]
    for (const [candidate, reason] of candidates) {
      assert.deepEqual(await validateAndSaveApiToken(candidate), { ok: false, reason })
      assert.equal(getApiToken(), OLD_TOKEN)
      assert.equal(values.get(TOKEN_KEY), OLD_TOKEN)
    }
    assert.deepEqual(events, [])
  })

  test('a rejected candidate preserves the existing token and does not emit a global auth event', async () => {
    const { values, events, timers } = browser()
    globalThis.fetch = async (url, options) => {
      assert.equal(url, '/api/intelligence-status')
      assert.equal(new Headers(options.headers).get('X-ICLE-Token'), NEW_TOKEN)
      return Response.json({ detail: 'ICLE_TOKEN required' }, { status: 401 })
    }
    assert.deepEqual(await validateAndSaveApiToken(NEW_TOKEN), { ok: false, reason: 'invalid' })
    assert.equal(getApiToken(), OLD_TOKEN)
    assert.equal(values.get(TOKEN_KEY), OLD_TOKEN)
    assert.deepEqual(events, [])
    assert.equal(timers.size, 0)
  })

  test('validation saves only after an authenticated API response and trims clipboard whitespace', async () => {
    const { values, timers } = browser()
    let respond
    globalThis.fetch = (url, options) => {
      assert.equal(url, '/api/intelligence-status')
      assert.equal(new Headers(options.headers).get('X-ICLE-Token'), NEW_TOKEN)
      assert.equal(options.cache, 'no-store')
      return new Promise((resolve) => { respond = resolve })
    }
    const validating = validateAndSaveApiToken(`\r\n ${NEW_TOKEN} \r\n`)
    assert.equal(getApiToken(), OLD_TOKEN)
    assert.equal(values.get(TOKEN_KEY), OLD_TOKEN)
    respond(statusResponse())
    assert.deepEqual(await validating, { ok: true, persistent: true })
    assert.equal(getApiToken(), NEW_TOKEN)
    assert.equal(values.get(TOKEN_KEY), NEW_TOKEN)
    assert.equal(timers.size, 0)
  })

  test('denied persistent storage still authenticates subsequent requests for the current page', async () => {
    const { storage, values } = browser()
    storage.setItem = () => { throw new DOMException('Storage blocked', 'SecurityError') }
    globalThis.fetch = async () => statusResponse()
    assert.deepEqual(await validateAndSaveApiToken(NEW_TOKEN), { ok: true, persistent: false })
    assert.equal(values.get(TOKEN_KEY), OLD_TOKEN)
    globalThis.fetch = async (url, options) => {
      assert.equal(new Headers(options.headers).get('X-ICLE-Token'), NEW_TOKEN)
      return statusResponse()
    }
    await api.intelligenceStatus()
  })

  test('silently ignored storage writes report session-only success rather than claiming persistence', async () => {
    const { storage, values } = browser()
    storage.setItem = () => {}
    globalThis.fetch = async () => statusResponse()
    assert.deepEqual(await validateAndSaveApiToken(NEW_TOKEN), { ok: true, persistent: false })
    assert.equal(values.get(TOKEN_KEY), OLD_TOKEN)
    assert.equal(getApiToken(), NEW_TOKEN)
  })

  test('network and server failures preserve credentials and leave no validation timers', async () => {
    const { values, timers, events } = browser()
    const cases = [
      [async () => { throw new TypeError('Failed to fetch') }, 'connection'],
      [async () => Response.json({ detail: 'Unavailable' }, { status: 503 }), 'server'],
      [async () => new Response('<html>Proxy fallback</html>', { headers: { 'Content-Type': 'text/html' } }), 'server'],
      [async () => Response.json({ status: 'ok' }), 'server'],
      [async () => Response.json(null), 'server'],
    ]
    for (const [fetchResponse, reason] of cases) {
      globalThis.fetch = fetchResponse
      assert.deepEqual(await validateAndSaveApiToken(NEW_TOKEN), { ok: false, reason })
      assert.equal(getApiToken(), OLD_TOKEN)
      assert.equal(values.get(TOKEN_KEY), OLD_TOKEN)
      assert.equal(timers.size, 0)
    }
    assert.deepEqual(events, [])
  })

  test('a stalled validation request is aborted and cannot replace the current credential', async () => {
    const { timers, values } = browser()
    let requestSignal
    globalThis.fetch = (_url, options) => new Promise((_resolve, reject) => {
      requestSignal = options.signal
      requestSignal.addEventListener('abort', () => reject(new DOMException('Aborted', 'AbortError')), { once: true })
    })
    const validating = validateAndSaveApiToken(NEW_TOKEN)
    assert.equal(requestSignal.aborted, false)
    for (const callback of timers.values()) callback()
    assert.deepEqual(await validating, { ok: false, reason: 'connection' })
    assert.equal(requestSignal.aborted, true)
    assert.equal(getApiToken(), OLD_TOKEN)
    assert.equal(values.get(TOKEN_KEY), OLD_TOKEN)
    assert.equal(timers.size, 0)
  })

  for (const [method, request] of [
    ['GET', () => api.intelligenceStatus()],
    ['POST', () => api.updateSettings({ default_language: 'zh' })],
    ['DELETE', () => api.deleteTask('synthetic-task')],
  ]) {
    test(`a late ${method} 401 from the previous credential cannot reopen authentication`, async () => {
      const { events } = browser()
      let respond
      globalThis.fetch = (_url, options) => {
        assert.equal(options.method ?? 'GET', method)
        assert.equal(new Headers(options.headers).get('X-ICLE-Token'), OLD_TOKEN)
        return new Promise((resolve) => { respond = resolve })
      }
      const pending = request()
      setApiToken(NEW_TOKEN)
      respond(Response.json({ detail: 'ICLE_TOKEN required' }, { status: 401 }))
      await assert.rejects(pending, /ICLE_TOKEN required/)
      assert.deepEqual(events, [])
    })
  }

  test('a 401 for the current credential still requests authentication', async () => {
    const { events } = browser()
    globalThis.fetch = async () => Response.json({ detail: 'ICLE_TOKEN required' }, { status: 401 })
    await assert.rejects(api.intelligenceStatus(), /ICLE_TOKEN required/)
    assert.deepEqual(events, ['icle-auth-required'])
  })
})
