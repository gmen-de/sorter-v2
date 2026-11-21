<script lang="ts">
	import { onDestroy, onMount } from 'svelte';
	import { _ } from 'svelte-i18n';
	import { getBackendHttpBase, machineHttpBaseUrlFromWsUrl } from '$lib/backend';
	import { getMachineContext } from '$lib/machines/context';
	import Alert from '$lib/components/ui/Alert.svelte';
	import Field from '$lib/components/ui/Field.svelte';
	import Select from '$lib/components/ui/Select.svelte';

	let { channelKey }: { channelKey: string } = $props();

	// A SettingRow's label and control share a line, which squeezes the label
	// out of a 20rem sidebar; these fields stack instead.
	const outputId = $derived(`led-output-${channelKey}`);
	const brightnessId = $derived(`led-brightness-${channelKey}`);

	const CHANNEL_LABELS: Record<string, string> = {
		c_channel_2: $_('components.settings.channelLedSection.channelLabel.cChannel2'),
		c_channel_3: $_('components.settings.channelLedSection.channelLabel.cChannel3'),
		classification_channel: $_('components.settings.channelLedSection.channelLabel.classificationCChannel')
	};
	// Applied while dragging the brightness slider so we don't POST per pixel.
	const BRIGHTNESS_DEBOUNCE_MS = 200;

	type LedOutput = { output_id: string; board_role: string; gpio: number };

	const machine = getMachineContext();

	let outputs = $state<LedOutput[]>([]);
	let assignments = $state<Record<string, string | null>>({});
	let brightness = $state<Record<string, number>>({});
	let loading = $state(true);
	let errorMsg = $state<string | null>(null);
	let debounce: ReturnType<typeof setTimeout> | null = null;

	const assigned = $derived(assignments[channelKey] ?? null);
	const percent = $derived(assigned ? (brightness[assigned] ?? 100) : 0);
	const sharedWith = $derived(
		Object.entries(assignments)
			.filter(([key, output]) => key !== channelKey && assigned !== null && output === assigned)
			.map(([key]) => CHANNEL_LABELS[key] ?? key)
	);

	function currentBackendBaseUrl(): string {
		return machineHttpBaseUrlFromWsUrl(machine.machine?.url) ?? getBackendHttpBase();
	}

	async function request(body: Record<string, unknown> | null) {
		try {
			const res = await fetch(
				`${currentBackendBaseUrl()}/api/leds`,
				body
					? {
							method: 'POST',
							headers: { 'Content-Type': 'application/json' },
							body: JSON.stringify(body)
						}
					: {}
			);
			if (!res.ok) throw new Error(await res.text());
			const payload = await res.json();
			outputs = payload?.outputs ?? [];
			assignments = payload?.assignments ?? {};
			brightness = payload?.brightness ?? {};
			errorMsg = null;
		} catch (e: any) {
			errorMsg = e?.message ?? $_('components.settings.channelLedSection.loadError');
		} finally {
			loading = false;
		}
	}

	function save(output: string | null, brightnessPercent: number) {
		void request({ channel: channelKey, output, brightness_percent: brightnessPercent });
	}

	function saveOutput(next: string | null) {
		// Adopt whatever the target pin is already running at, so picking a GPIO a
		// sibling channel is using does not yank its brightness.
		save(next, next ? (brightness[next] ?? 100) : 100);
	}

	function saveBrightness(next: number) {
		if (!assigned) return;
		const clamped = Math.max(0, Math.min(100, Math.round(next)));
		brightness = { ...brightness, [assigned]: clamped };
		if (debounce) clearTimeout(debounce);
		debounce = setTimeout(() => save(assigned, clamped), BRIGHTNESS_DEBOUNCE_MS);
	}

	onMount(() => void request(null));
	onDestroy(() => {
		if (debounce) clearTimeout(debounce);
	});
</script>

<div class="flex flex-col gap-4" class:opacity-50={!loading && outputs.length === 0}>
	{#if errorMsg}
		<Alert tone="danger">{errorMsg}</Alert>
	{:else if !loading && outputs.length === 0}
		<Alert tone="info">{$_('components.settings.channelLedSection.noOutputsFree')}</Alert>
	{/if}
	<Field
		label={$_('components.settings.channelLedSection.output.label')}
		for={outputId}
		help={$_('components.settings.channelLedSection.output.help')}
	>
		<Select
			id={outputId}
			value={assigned ?? ''}
			disabled={loading || outputs.length === 0}
			options={[
				{ value: '', label: $_('components.settings.channelLedSection.output.notAssigned') },
				...outputs.map((output) => ({
					value: output.output_id,
					label: $_('components.settings.channelLedSection.output.optionLabel', {
						values: { role: output.board_role, gpio: output.gpio }
					})
				}))
			]}
			onchange={(id) => saveOutput(id || null)}
		/>
	</Field>
	<Field
		label={$_('components.settings.channelLedSection.brightness.label')}
		for={brightnessId}
		help={$_('components.settings.channelLedSection.brightness.help')}
	>
		<div class="flex items-center gap-3">
			<input
				id={brightnessId}
				type="range"
				min="0"
				max="100"
				step="1"
				value={percent}
				disabled={loading || !assigned}
				oninput={(e) => saveBrightness(Number(e.currentTarget.value))}
				class="min-w-0 flex-1 accent-primary"
			/>
			<span class="num w-10 shrink-0 text-right text-sm text-ink">{percent}%</span>
		</div>
	</Field>
	{#if sharedWith.length > 0}
		<p class="text-sm text-ink-muted">
			{$_('components.settings.channelLedSection.sharedWith', {
				values: { names: sharedWith.join(', ') }
			})}
		</p>
	{/if}
</div>
