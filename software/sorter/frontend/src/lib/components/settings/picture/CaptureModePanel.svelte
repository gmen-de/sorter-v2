<script lang="ts">
	import { _ } from 'svelte-i18n';
	import { getBackendHttpBase } from '$lib/backend';
	import Alert from '$lib/components/ui/Alert.svelte';
	import Spinner from '$lib/components/ui/Spinner.svelte';
	import Field from '$lib/components/ui/Field.svelte';
	import Select from '$lib/components/ui/Select.svelte';
	import type { CameraRole } from '$lib/settings/stations';

	type CaptureMode = {
		width: number;
		height: number;
		fps: number;
		fourcc: string;
		native_fourcc?: string;
	};

	type CaptureModeResponse = {
		ok: boolean;
		role: string;
		source?: number | string | null;
		supported: boolean;
		backend?: string;
		modes: CaptureMode[];
		current?: { width?: number | null; height?: number | null; fps?: number | null; fourcc?: string | null } | null;
		live?: { width?: number | null; height?: number | null; fps?: number | null } | null;
		message?: string;
	};

	let { role }: { role: CameraRole } = $props();

	let loading = $state(true);
	let saving = $state(false);
	let error = $state<string | null>(null);
	let status = $state('');
	let data = $state<CaptureModeResponse | null>(null);
	let selectedModeKey = $state<string>('');
	let selectedFourcc = $state<string>('');
	let fourccOptions = $derived(fourccsForModeKey(selectedModeKey));

	function modeKey(m: { width?: number | null; height?: number | null; fps?: number | null } | null | undefined): string {
		if (!m || !m.width || !m.height || !m.fps) return '';
		return `${m.width}x${m.height}@${m.fps}`;
	}

	function resolutionKey(m: { width?: number | null; height?: number | null } | null | undefined): string {
		if (!m || !m.width || !m.height) return '';
		return `${m.width}x${m.height}`;
	}

	function fourccsForModeKey(key: string): string[] {
		if (!data) return [];
		const seen = new Set<string>();
		const out: string[] = [];
		for (const m of data.modes) {
			if (modeKey(m) !== key) continue;
			const fc = (m.fourcc || '').toUpperCase();
			if (!fc || seen.has(fc)) continue;
			seen.add(fc);
			out.push(fc);
		}
		out.sort((a, b) => (a === 'MJPG' ? -1 : b === 'MJPG' ? 1 : a.localeCompare(b)));
		return out;
	}

	function pickInitialFourcc(key: string, current: string | null | undefined): string {
		const options = fourccsForModeKey(key);
		if (options.length === 0) return '';
		const want = (current || '').toUpperCase();
		if (want && options.includes(want)) return want;
		return options.includes('MJPG') ? 'MJPG' : options[0];
	}

	function pickInitialModeKey(currentWidth: number | null | undefined, currentHeight: number | null | undefined, currentFps: number | null | undefined): string {
		if (!data) return '';
		const wantRes = resolutionKey({ width: currentWidth, height: currentHeight });
		const wantFps = currentFps ?? 0;
		// Exact match first
		const exact = data.modes.find((m) => resolutionKey(m) === wantRes && m.fps === wantFps);
		if (exact) return modeKey(exact);
		// Same resolution, highest fps
		const sameRes = data.modes.filter((m) => resolutionKey(m) === wantRes).sort((a, b) => b.fps - a.fps);
		if (sameRes.length > 0) return modeKey(sameRes[0]);
		// First mode overall
		return data.modes.length > 0 ? modeKey(data.modes[0]) : '';
	}

	async function load() {
		loading = true;
		error = null;
		status = '';
		try {
			const res = await fetch(`${getBackendHttpBase()}/api/cameras/capture-modes/${role}`, {
				cache: 'no-store'
			});
			if (!res.ok) throw new Error(await res.text());
			const parsed = (await res.json()) as CaptureModeResponse;
			// Uncompressed modes (YUYV) fill the USB bus the cameras share, so a
			// camera that can do MJPEG is only offered MJPEG.
			const mjpeg = parsed.modes.filter((m) => (m.fourcc || '').toUpperCase() === 'MJPG');
			data = mjpeg.length ? { ...parsed, modes: mjpeg } : parsed;
			selectedModeKey = pickInitialModeKey(
				parsed.current?.width ?? parsed.live?.width ?? null,
				parsed.current?.height ?? parsed.live?.height ?? null,
				parsed.current?.fps ?? null,
			);
			selectedFourcc = pickInitialFourcc(selectedModeKey, parsed.current?.fourcc ?? null);
		} catch (e: any) {
			error = e.message ?? $_('components.settings.picture.captureModePanel.failedToLoad');
		} finally {
			loading = false;
		}
	}

	async function save(modeKeyStr: string, fourcc: string) {
		if (!data) return;
		const fcUpper = (fourcc || '').toUpperCase();
		const mode =
			data.modes.find((m) => modeKey(m) === modeKeyStr && (m.fourcc || '').toUpperCase() === fcUpper) ??
			data.modes.find((m) => modeKey(m) === modeKeyStr);
		if (!mode) return;
		saving = true;
		error = null;
		status = '';
		try {
			const res = await fetch(`${getBackendHttpBase()}/api/cameras/capture-modes/${role}`, {
				method: 'POST',
				headers: { 'Content-Type': 'application/json' },
				body: JSON.stringify({ width: mode.width, height: mode.height, fps: mode.fps, fourcc: fcUpper || mode.fourcc })
			});
			if (!res.ok) throw new Error(await res.text());
			const parsed = await res.json();
			status = parsed.message ?? $_('components.settings.picture.captureModePanel.savedStatus');
			selectedModeKey = modeKeyStr;
			selectedFourcc = fcUpper;
			await load();
		} catch (e: any) {
			error = e.message ?? $_('components.settings.picture.captureModePanel.failedToSave');
		} finally {
			saving = false;
		}
	}

	function onModeChange(value: string) {
		if (!value || value === selectedModeKey) return;
		const nextFourcc = pickInitialFourcc(value, selectedFourcc);
		void save(value, nextFourcc);
	}

	function onFourccChange(value: string) {
		if (!value || value === selectedFourcc) return;
		void save(selectedModeKey, value);
	}

	$effect(() => {
		void role;
		void load();
	});
	const modeOptions = $derived(
		Array.from(new Set((data?.modes ?? []).map((m) => modeKey(m)))).flatMap((key) => {
			const sample = data?.modes.find((m) => modeKey(m) === key);
			return sample
				? [
						{
							value: key,
							label: $_('components.settings.picture.captureModePanel.modeOptionLabel', {
								values: { width: sample.width, height: sample.height, fps: sample.fps }
							})
						}
					]
				: [];
		})
	);
</script>

<section class="flex flex-col gap-3 px-(--pad-panel) py-4">
	<div class="flex items-baseline justify-between gap-2">
		<h3 class="label">{$_('components.settings.picture.captureModePanel.title')}</h3>
		{#if data?.live?.width && data?.live?.height}
			<span class="num text-sm text-ink-muted">
				{$_('components.settings.picture.captureModePanel.liveResolution', {
					values: { width: data.live.width, height: data.live.height }
				})}{#if data.live.fps}{$_('components.settings.picture.captureModePanel.liveFpsSuffix', {
						values: { fps: data.live.fps }
					})}{/if}
			</span>
		{/if}
	</div>
	{#if error}<Alert tone="danger">{error}</Alert>{/if}
	{#if loading}
		<p class="flex items-center gap-2 text-sm text-ink-muted">
			<Spinner size={14} />
			{$_('components.settings.picture.captureModePanel.loadingText')}
		</p>
	{:else if !data?.supported}
		<p class="text-sm text-ink-muted">
			{data?.message ?? $_('components.settings.picture.captureModePanel.noResolutionChoice')}
		</p>
	{:else}
		<Field label={$_('components.settings.picture.captureModePanel.modeFieldLabel')} for="capture-mode-{role}">
			<Select
				id="capture-mode-{role}"
				value={selectedModeKey}
				options={modeOptions}
				placeholder={$_('components.settings.picture.captureModePanel.pickModePlaceholder')}
				disabled={saving}
				onchange={onModeChange}
			/>
		</Field>
		<Field
			label={$_('components.settings.picture.captureModePanel.pixelFormatField.label')}
			for="capture-fourcc-{role}"
			help={$_('components.settings.picture.captureModePanel.pixelFormatField.help')}
		>
			<Select
				id="capture-fourcc-{role}"
				value={selectedFourcc}
				options={fourccOptions.map((fc) => ({
					value: fc,
					label:
						fc === 'MJPG' ? $_('components.settings.picture.captureModePanel.mjpgDefaultLabel') : fc
				}))}
				placeholder={$_('components.settings.picture.captureModePanel.nonePlaceholder')}
				disabled={saving || fourccOptions.length === 0}
				onchange={onFourccChange}
			/>
		</Field>
		{#if status}<p class="text-sm text-ink-muted">{status}</p>{/if}
	{/if}
</section>
