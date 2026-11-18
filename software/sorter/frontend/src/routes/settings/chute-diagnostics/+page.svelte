<script lang="ts">
	import { getBackendHttpBase, machineHttpBaseUrlFromWsUrl } from '$lib/backend';
	import { getMachinesContext } from '$lib/machines/context';
	import Alert from '$lib/components/ui/Alert.svelte';
	import Badge from '$lib/components/ui/Badge.svelte';
	import Field from '$lib/components/ui/Field.svelte';
	import Panel from '$lib/components/ui/Panel.svelte';
	import PageHeader from '$lib/components/ui/PageHeader.svelte';
	import Select from '$lib/components/ui/Select.svelte';
	import Stat from '$lib/components/ui/Stat.svelte';
	import BinLayoutViz from '../chute-aiming/BinLayoutViz.svelte';

	// A live readout + clickable bin wheel for the Octopus's magnet-ring
	// encoder and TMC driver — the settings-page equivalent of chute_debug_ui.py's
	// readout + bin wheel, built against the endpoints chute.py's live status and
	// bins.py's move-to/layout routes already expose (no new backend surface).

	const manager = getMachinesContext();
	const POLL_MS = 400;

	type Layer = { layer_index: number; bin_count: number; bins: { section_index: number; bin_index: number }[] };
	type LiveStatus = {
		live_available: boolean;
		chute_position_sensor: string | null;
		current_angle: number | null;
		stepper_stopped: boolean | null;
		homed: boolean | null;
		encoder_count: number | null;
		encoder_index_synced: boolean | null;
		encoder_count_at_index?: number | null;
		encoder_raw_sensor_a?: boolean | null;
		encoder_raw_sensor_b?: boolean | null;
		homing_active?: boolean | null;
		homing_failed?: boolean | null;
		tmc_sg_result?: number | null;
		tmc_drv_status?: Record<string, unknown> | null;
		stall_latched?: boolean | null;
		encoder_error?: string;
	};

	let layers = $state<Layer[]>([]);
	let layerIndex = $state(0);
	let live = $state<LiveStatus | null>(null);
	let numSections = $state(6);
	let sectionWidthDeg = $state(51.75);
	let firstSectionOffsetDeg = $state(8.25);
	let maxAngleDeg = $state(350);

	let errorMsg = $state<string | null>(null);
	let statusMsg = $state('');
	let jogging = $state(false);
	let loadedMachineKey = $state('');
	let pollHandle: ReturnType<typeof setInterval> | null = null;
	let liveInFlight = false;

	function backendBase(): string {
		return (
			machineHttpBaseUrlFromWsUrl(
				manager.selectedMachine?.status === 'connected' ? manager.selectedMachine.url : null
			) ?? getBackendHttpBase()
		);
	}

	const binCountForLayer = $derived(layers.find((l) => l.layer_index === layerIndex)?.bin_count ?? 1);
	// Sections per layer is machine-wide (numSections), so bins-per-section is
	// the layer's total divided by it — matches chute.py's own k = num_bins/n.
	const binsPerSection = $derived(Math.max(1, Math.round(binCountForLayer / Math.max(1, numSections))));

	async function loadLayout() {
		try {
			const res = await fetch(`${backendBase()}/api/bins/layout`);
			if (!res.ok) throw new Error(await res.text());
			const data = await res.json();
			layers = Array.isArray(data?.layers)
				? data.layers.map((l: any) => ({ layer_index: l.layer_index, bin_count: l.bin_count, bins: l.bins }))
				: [];
			if (!layers.find((l) => l.layer_index === layerIndex) && layers.length) layerIndex = layers[0].layer_index;
		} catch (e: any) {
			errorMsg = e.message ?? 'Failed to load the bin layout';
		}
	}

	async function loadGeometry() {
		try {
			const res = await fetch(`${backendBase()}/api/hardware-config/chute`);
			if (!res.ok) throw new Error(await res.text());
			const c = await res.json();
			if (Number.isFinite(c?.num_sections)) numSections = c.num_sections;
			if (Number.isFinite(c?.section_width_deg)) sectionWidthDeg = c.section_width_deg;
			if (Number.isFinite(c?.first_section_offset_deg)) firstSectionOffsetDeg = c.first_section_offset_deg;
			if (Number.isFinite(c?.max_angle_deg)) maxAngleDeg = c.max_angle_deg;
		} catch (e: any) {
			errorMsg = e.message ?? 'Failed to load the chute geometry';
		}
	}

	async function loadLive() {
		if (liveInFlight) return;
		liveInFlight = true;
		try {
			const res = await fetch(`${backendBase()}/api/hardware-config/chute/live`);
			if (!res.ok) throw new Error(await res.text());
			live = await res.json();
		} catch {
			// transient poll failure — keep showing the last good reading
		} finally {
			liveInFlight = false;
		}
	}

	async function jogTo(sel: { section: number; bin: number }) {
		if (jogging) return;
		jogging = true;
		errorMsg = null;
		try {
			const res = await fetch(`${backendBase()}/api/bins/move-to`, {
				method: 'POST',
				headers: { 'Content-Type': 'application/json' },
				body: JSON.stringify({ layer_index: layerIndex, section_index: sel.section, bin_index: sel.bin })
			});
			if (!res.ok) {
				const detail = await res.json().catch(() => null);
				throw new Error(detail?.detail ?? `HTTP ${res.status}`);
			}
			statusMsg = `Moving to section ${sel.section + 1}, bin ${sel.bin + 1}.`;
		} catch (e: any) {
			errorMsg = e.message ?? 'Failed to move the chute';
		} finally {
			jogging = false;
		}
	}

	function fmt(v: number | null | undefined, d = 1): string {
		return v === null || v === undefined || !Number.isFinite(v) ? '--' : v.toFixed(d);
	}
	function tri(v: boolean | null | undefined): string {
		return v === null || v === undefined ? '--' : v ? 'Yes' : 'No';
	}
	function drvStatusSummary(d: Record<string, unknown> | null | undefined): string {
		if (!d) return '--';
		const flags = ['otpw', 'ot', 's2ga', 's2gb', 's2vsa', 's2vsb', 'ola', 'olb'].filter((k) => d[k]);
		return flags.length ? flags.join(', ') : 'clean';
	}

	function startPolling() {
		if (pollHandle !== null) return;
		pollHandle = setInterval(() => void loadLive(), POLL_MS);
	}
	function stopPolling() {
		if (pollHandle !== null) {
			clearInterval(pollHandle);
			pollHandle = null;
		}
	}

	$effect(() => {
		const key = manager.selectedMachine?.url ?? '';
		if (key === loadedMachineKey) return;
		loadedMachineKey = key;
		void loadLayout();
		void loadGeometry();
		void loadLive();
	});

	$effect(() => {
		startPolling();
		return () => stopPolling();
	});
</script>

<PageHeader title="Chute diagnostics" description="Live encoder/driver readout and a clickable bin wheel for jogging the chute by hand." />

{#if errorMsg}
	<Alert tone="danger">{errorMsg}</Alert>
{/if}

<Panel title="Live readout" description="Raw sensor state and driver registers, polled while this page is open.">
	{#if !live?.live_available}
		<div class="px-(--pad-panel) pb-(--pad-panel) text-sm text-ink-muted">
			Hardware not initialized — start the machine to see live chute data.
		</div>
	{:else}
		<div class="grid grid-cols-2 gap-3 px-(--pad-panel) pb-(--pad-panel) sm:grid-cols-4">
			<Stat label="Current angle" value={`${fmt(live.current_angle)}°`} />
			<Stat label="Stepper stopped" value={tri(live.stepper_stopped)} />
			<Stat label="Homed" value={tri(live.homed)} />
			<Stat label="Encoder count" value={live.encoder_count ?? '--'} />
			<Stat label="Index synced" value={tri(live.encoder_index_synced)} />
			<Stat label="Count at index" value={live.encoder_count_at_index ?? '--'} />
			<Stat label="Sensor A / B" value={`${tri(live.encoder_raw_sensor_a)} / ${tri(live.encoder_raw_sensor_b)}`} />
			<Stat label="Homing" value={live.homing_active ? 'Active' : live.homing_failed ? 'Failed' : 'Idle'} />
		</div>
		<div class="flex flex-wrap gap-3 px-(--pad-panel) pb-(--pad-panel)">
			<Badge tone={live.stall_latched ? 'danger' : 'neutral'}>
				Stall latch: {tri(live.stall_latched)}
			</Badge>
			<Badge>SG_RESULT: {live.tmc_sg_result ?? '--'}</Badge>
			<Badge tone={drvStatusSummary(live.tmc_drv_status) === 'clean' ? 'neutral' : 'danger'}>
				DRV_STATUS: {drvStatusSummary(live.tmc_drv_status)}
			</Badge>
			{#if live.encoder_error}<Badge tone="danger">{live.encoder_error}</Badge>{/if}
		</div>
	{/if}
</Panel>

<Panel title="Bin wheel" description="Click a bin to jog the chute straight there (same move the sorter itself uses).">
	<div class="flex flex-col gap-3 px-(--pad-panel) pb-(--pad-panel)">
		<Field label="Layer" for="diag-layer">
			<Select
				id="diag-layer"
				value={String(layerIndex)}
				onchange={(v) => (layerIndex = Number(v))}
				options={layers.map((l) => ({ value: String(l.layer_index), label: `Layer ${l.layer_index + 1}` }))}
			/>
		</Field>
		<div class="max-w-xs">
			<BinLayoutViz
				{numSections}
				{sectionWidthDeg}
				{firstSectionOffsetDeg}
				{maxAngleDeg}
				binCount={binsPerSection}
				liveAngleDeg={live?.current_angle ?? null}
				onSelect={(sel) => void jogTo(sel)}
			/>
		</div>
		{#if statusMsg}<p class="text-sm text-ink-muted">{statusMsg}</p>{/if}
	</div>
</Panel>
