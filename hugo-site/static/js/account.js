(() => {
  // 本地叶签：无账号、无后端、无网络请求，仅用 localStorage。
  const KEY = 'iris.bookmarks.v1';
  const state = { user: { login: 'local' }, csrf: '', oauth_ready: false, mcp_url: '', local: true };
  const read = () => { try { const raw = localStorage.getItem(KEY); return raw ? JSON.parse(raw) : []; } catch { return []; } };
  const write = (list) => { try { localStorage.setItem(KEY, JSON.stringify(list)); } catch {} };
  const ready = Promise.resolve(state);
  async function request(path, options = {}) {
    if (path === '/api/bookmarks') {
      if (!options.method || options.method === 'GET') return { bookmarks: read() };
      const body = JSON.parse(options.body || '{}');
      const list = new Set(read());
      if (options.method === 'POST') list.add(body.url); else list.delete(body.url);
      write([...list]);
      return { ok: true };
    }
    throw Error('本地模式不支持该操作');
  }
  function login() {}
  async function setBookmark(url, saved) {
    const list = new Set(read());
    if (saved) list.add(url); else list.delete(url);
    write([...list]);
    return true;
  }
  window.KnowledgeAccount = { state, ready, request, login, setBookmark };
})();
