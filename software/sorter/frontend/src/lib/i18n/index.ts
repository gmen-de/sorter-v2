// Locale catalogs are split one JSON file per migration batch, per locale
// (locales/en/<batch-id>.json, locales/de/<batch-id>.json) instead of one
// shared file per locale: register() merges them, so batches touching their
// own pair of files can be migrated in parallel with zero merge conflicts.
// Key convention mirrors each source file's own path, e.g.
// src/routes/settings/appearance/+page.svelte -> routes.settings.appearance.*
import { init, register, getLocaleFromNavigator, waitLocale, locale } from 'svelte-i18n';

export const SUPPORTED_LOCALES = ['en', 'de'] as const;
export type SupportedLocale = (typeof SUPPORTED_LOCALES)[number];

const LOCALE_STORAGE_KEY = 'sorter-locale';

const modules = import.meta.glob<{ default: Record<string, unknown> }>('./locales/*/*.json');
for (const path in modules) {
	const match = path.match(/\.\/locales\/([a-z]{2})\//);
	if (!match) continue;
	register(match[1], () => modules[path]().then((m) => m.default));
}

function isSupportedLocale(value: string | null | undefined): value is SupportedLocale {
	return (SUPPORTED_LOCALES as readonly string[]).includes(value ?? '');
}

export function setupI18n(): Promise<void> {
	let stored: string | null = null;
	try {
		stored = localStorage.getItem(LOCALE_STORAGE_KEY);
	} catch {
		// localStorage unavailable (private mode, etc.) - fall through.
	}
	const nav = getLocaleFromNavigator()?.slice(0, 2);
	const initialLocale = [stored, nav].find(isSupportedLocale) ?? 'en';
	init({ fallbackLocale: 'en', initialLocale });
	return waitLocale();
}

export function setLocale(loc: SupportedLocale): void {
	locale.set(loc);
	try {
		localStorage.setItem(LOCALE_STORAGE_KEY, loc);
	} catch {
		// localStorage unavailable - the choice just won't survive a reload.
	}
}

export { locale };
