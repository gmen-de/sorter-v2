const BACKEND_PORT = 8000;

// A page served over HTTPS on the default port sits behind a reverse proxy
// (e.g. HAProxy with a login in front). The proxy has only the one port, so it
// passes this path on to the backend's port 8000 with the prefix taken off.
const PROXIED_BACKEND_PATH = '/backend';

export function getBackendHttpBase(): string {
	if (typeof window === 'undefined') return `http://localhost:${BACKEND_PORT}`;
	const { protocol, hostname, port, origin } = window.location;
	if (protocol === 'https:' && port === '') return `${origin}${PROXIED_BACKEND_PATH}`;
	return `${protocol}//${hostname}:${BACKEND_PORT}`;
}

export function getBackendWsBase(): string {
	return getBackendHttpBase().replace(/^http/, 'ws');
}

// The path before /ws is kept, so a backend behind the proxy's /backend
// prefix keeps it in its HTTP base too.
export function machineHttpBaseUrlFromWsUrl(wsUrl: string | null | undefined): string | null {
	if (!wsUrl) return null;
	try {
		const parsed = new URL(wsUrl);
		const protocol = parsed.protocol === 'wss:' ? 'https:' : 'http:';
		const basePath = parsed.pathname.replace(/\/ws\/?$/, '').replace(/\/$/, '');
		return `${protocol}//${parsed.host}${basePath}`;
	} catch {
		return null;
	}
}

export function machineWsUrlFromHttpBaseUrl(
	backendBaseUrl: string | null | undefined
): string | null {
	if (!backendBaseUrl) return null;
	try {
		const parsed = new URL(backendBaseUrl);
		parsed.protocol = parsed.protocol === 'https:' ? 'wss:' : 'ws:';
		parsed.pathname = `${parsed.pathname.replace(/\/$/, '')}/ws`;
		parsed.search = '';
		parsed.hash = '';
		return parsed.toString();
	} catch {
		return null;
	}
}

// The supervisor that served this page restarts its backend even when the
// backend no longer answers. A backend on another host, or a page from the
// dev server, is asked to exit instead, and its supervisor starts it again.
export async function requestBackendRestart(
	backendBaseUrl: string,
	timeoutMs = 4000
): Promise<boolean> {
	const urls = [`${backendBaseUrl}/api/system/restart`];
	if (new URL(backendBaseUrl).hostname === window.location.hostname) {
		urls.unshift('/api/supervisor/restart');
	}
	for (const url of urls) {
		try {
			const response = await fetch(url, { method: 'POST', signal: AbortSignal.timeout(timeoutMs) });
			if (response.ok) return true;
		} catch {
			// the next way, if there is one
		}
	}
	return false;
}
