<script lang="ts">
	import { _ } from 'svelte-i18n';
	import { getBackendHttpBase, machineHttpBaseUrlFromWsUrl } from '$lib/backend';
	import { getMachinesContext } from '$lib/machines/context';
	import Alert from '$lib/components/ui/Alert.svelte';
	import Badge from '$lib/components/ui/Badge.svelte';
	import Field from '$lib/components/ui/Field.svelte';
	import Input from '$lib/components/ui/Input.svelte';
	import Panel from '$lib/components/ui/Panel.svelte';
	import PageHeader from '$lib/components/ui/PageHeader.svelte';
	import Select from '$lib/components/ui/Select.svelte';
	import Stat from '$lib/components/ui/Stat.svelte';
	import Tabs from '$lib/components/ui/Tabs.svelte';
	import StepperPanel from '$lib/components/settings/StepperPanel.svelte';
	import ChannelLedSection from '$lib/components/settings/ChannelLedSection.svelte';
	import BenchmarkPanel from '$lib/components/settings/BenchmarkPanel.svelte';
	import Button from '$lib/components/ui/Button.svelte';
	import BinLayoutViz from '../chute-aiming/BinLayoutViz.svelte';

	// Everything on the Octopus board in one place: the chute's live encoder/
	// driver readout + bin wheel (the settings-page equivalent of
	// chute_debug_ui.py), the other three steppers it also carries
	// (c_channel_1/2/3 rotors, carousel), and the LED channels it drives.
	// Nothing here is new backend surface except the chute tab - the Steppers
	// and LEDs tabs just reuse the panels those already have elsewhere.

	const manager = getMachinesContext();
	const POLL_MS = 400;

	type Tab = 'chute' | 'steppers' | 'leds' | 'fans' | 'benchmark';
	let tab = $state<Tab>('chute');

	type Fan = { index: number; duty_percent: number; auto?: boolean };
	type CpuInfo = {
		temp_c: number | null;
		throttle_c: number;
		fan_off_below_c: number;
		fan_on_at_or_above_c: number;
		fan_on: boolean | null;
		auto_fan_index: number;
		min_switch_interval_s: number;
	};
	let fansAvailable = $state(false);
	let fans = $state<Fan[]>([]);
	let cpu = $state<CpuInfo | null>(null);
	let fansLoading = $state(true);
	let savingFan = $state<number | null>(null);

	type Layer = { layer_index: number; bin_count: number };
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
	// Only meaningful in "encoder_native" mode - see DEFAULT_HOME_CORRECTION_USTEPS
	// in hardware_settings/chute.py.
	let homeCorrectionUsteps = $state<number | null>(null);
	let homeCorrectionInput = $state('');
	let homeCorrectionSaving = $state(false);

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
	const binsPerSection = $derived(Math.max(1, Math.round(binCountForLayer / Math.max(1, numSections))));

	async function loadLayout() {
		try {
			const res = await fetch(`${backendBase()}/api/bins/layout`);
			if (!res.ok) throw new Error(await res.text());
			const data = await res.json();
			layers = Array.isArray(data?.layers)
				? data.layers.map((l: any) => ({ layer_index: l.layer_index, bin_count: l.bin_count }))
				: [];
			if (!layers.find((l) => l.layer_index === layerIndex) && layers.length) layerIndex = layers[0].layer_index;
		} catch (e: any) {
			errorMsg = e.message ?? $_('routes.settings.octopusDiagnostic.errors.loadLayout');
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
			if (Number.isFinite(c?.home_correction_usteps)) {
				homeCorrectionUsteps = c.home_correction_usteps;
				homeCorrectionInput = String(c.home_correction_usteps);
			}
		} catch (e: any) {
			errorMsg = e.message ?? $_('routes.settings.octopusDiagnostic.errors.loadGeometry');
		}
	}

	async function saveHomeCorrection() {
		const value = Number(homeCorrectionInput);
		if (!Number.isInteger(value)) {
			errorMsg = $_('routes.settings.octopusDiagnostic.homeCorrection.invalid');
			return;
		}
		homeCorrectionSaving = true;
		errorMsg = null;
		try {
			const res = await fetch(`${backendBase()}/api/hardware-config/chute`, {
				method: 'POST',
				headers: { 'Content-Type': 'application/json' },
				body: JSON.stringify({ home_correction_usteps: value })
			});
			if (!res.ok) {
				const detail = await res.json().catch(() => null);
				throw new Error(detail?.detail ?? `HTTP ${res.status}`);
			}
			homeCorrectionUsteps = value;
			statusMsg = $_('routes.settings.octopusDiagnostic.homeCorrection.saved');
		} catch (e: any) {
			errorMsg = e.message ?? $_('routes.settings.octopusDiagnostic.homeCorrection.saveFailed');
		} finally {
			homeCorrectionSaving = false;
		}
	}

	let fansInFlight = false;
	async function loadFans() {
		if (fansInFlight) return;
		fansInFlight = true;
		try {
			const res = await fetch(`${backendBase()}/api/fans`);
			if (!res.ok) throw new Error(await res.text());
			const data = await res.json();
			fansAvailable = Boolean(data?.available);
			fans = Array.isArray(data?.fans) ? data.fans : [];
			cpu = data?.cpu ?? null;
		} catch (e: any) {
			// Only surface the error on the initial load - a transient poll
			// failure should keep showing the last good reading.
			if (fansLoading) errorMsg = e.message ?? $_('routes.settings.octopusDiagnostic.errors.loadFans');
		} finally {
			fansLoading = false;
			fansInFlight = false;
		}
	}

	async function setFan(index: number, dutyPercent: number) {
		savingFan = index;
		try {
			const res = await fetch(`${backendBase()}/api/fans`, {
				method: 'POST',
				headers: { 'Content-Type': 'application/json' },
				body: JSON.stringify({ index, duty_percent: dutyPercent })
			});
			if (!res.ok) {
				const detail = await res.json().catch(() => null);
				throw new Error(detail?.detail ?? `HTTP ${res.status}`);
			}
			fans = fans.map((f) => (f.index === index ? { ...f, duty_percent: dutyPercent } : f));
		} catch (e: any) {
			errorMsg = e.message ?? $_('routes.settings.octopusDiagnostic.errors.setFan');
		} finally {
			savingFan = null;
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
			statusMsg = $_('routes.settings.octopusDiagnostic.binWheel.movingTo', { values: { section: sel.section + 1, bin: sel.bin + 1 } });
		} catch (e: any) {
			errorMsg = e.message ?? $_('routes.settings.octopusDiagnostic.errors.moveChute');
		} finally {
			jogging = false;
		}
	}

	function fmt(v: number | null | undefined, d = 1): string {
		return v === null || v === undefined || !Number.isFinite(v) ? '--' : v.toFixed(d);
	}
	function tri(v: boolean | null | undefined): string {
		return v === null || v === undefined
			? '--'
			: v
				? $_('routes.settings.octopusDiagnostic.live.yes')
				: $_('routes.settings.octopusDiagnostic.live.no');
	}
	function drvStatusSummary(d: Record<string, unknown> | null | undefined): string {
		if (!d) return '--';
		const flags = ['otpw', 'ot', 's2ga', 's2gb', 's2vsa', 's2vsb', 'ola', 'olb'].filter((k) => d[k]);
		return flags.length ? flags.join(', ') : $_('routes.settings.octopusDiagnostic.live.clean');
	}

	function startPolling() {
		if (pollHandle !== null) return;
		pollHandle = setInterval(() => {
			if (tab === 'chute') void loadLive();
			else if (tab === 'fans') void loadFans();
		}, POLL_MS);
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
		void loadFans();
	});

	$effect(() => {
		startPolling();
		return () => stopPolling();
	});
</script>

<PageHeader
	title={$_('routes.settings.octopusDiagnostic.title')}
	description={$_('routes.settings.octopusDiagnostic.description')}
/>

{#if errorMsg}
	<Alert tone="danger">{errorMsg}</Alert>
{/if}

<Tabs
	label={$_('routes.settings.octopusDiagnostic.tabs.ariaLabel')}
	bind:value={tab}
	items={[
		{ value: 'chute', label: $_('routes.settings.octopusDiagnostic.tabs.chute') },
		{ value: 'steppers', label: $_('routes.settings.octopusDiagnostic.tabs.steppers') },
		{ value: 'leds', label: $_('routes.settings.octopusDiagnostic.tabs.leds') },
		{ value: 'fans', label: $_('routes.settings.octopusDiagnostic.tabs.fans') },
		{ value: 'benchmark', label: $_('routes.settings.octopusDiagnostic.tabs.benchmark') }
	]}
/>

{#if tab === 'chute'}
	<Panel
		title={$_('routes.settings.octopusDiagnostic.live.panelTitle')}
		description={$_('routes.settings.octopusDiagnostic.live.panelDescription')}
	>
		{#if !live?.live_available}
			<div class="px-(--pad-panel) pb-(--pad-panel) text-sm text-ink-muted">
				{$_('routes.settings.octopusDiagnostic.live.notAvailable')}
			</div>
		{:else}
			<div class="grid grid-cols-2 gap-3 px-(--pad-panel) pb-(--pad-panel) sm:grid-cols-4">
				<Stat label={$_('routes.settings.octopusDiagnostic.live.stat.currentAngle')} value={`${fmt(live.current_angle)}°`} />
				<Stat label={$_('routes.settings.octopusDiagnostic.live.stat.stepperStopped')} value={tri(live.stepper_stopped)} />
				<Stat label={$_('routes.settings.octopusDiagnostic.live.stat.homed')} value={tri(live.homed)} />
				<Stat label={$_('routes.settings.octopusDiagnostic.live.stat.encoderCount')} value={live.encoder_count ?? '--'} />
				<Stat label={$_('routes.settings.octopusDiagnostic.live.stat.indexSynced')} value={tri(live.encoder_index_synced)} />
				<Stat label={$_('routes.settings.octopusDiagnostic.live.stat.countAtIndex')} value={live.encoder_count_at_index ?? '--'} />
				<Stat label={$_('routes.settings.octopusDiagnostic.live.stat.sensorAB')} value={`${tri(live.encoder_raw_sensor_a)} / ${tri(live.encoder_raw_sensor_b)}`} />
				<Stat
					label={$_('routes.settings.octopusDiagnostic.live.stat.homing')}
					value={live.homing_active
						? $_('routes.settings.octopusDiagnostic.live.homingActive')
						: live.homing_failed
							? $_('routes.settings.octopusDiagnostic.live.homingFailed')
							: $_('routes.settings.octopusDiagnostic.live.homingIdle')}
				/>
			</div>
			<div class="flex flex-wrap gap-3 px-(--pad-panel) pb-(--pad-panel)">
				<Badge tone={live.stall_latched ? 'danger' : 'neutral'}>
					{$_('routes.settings.octopusDiagnostic.live.stallLatch', { values: { value: tri(live.stall_latched) } })}
				</Badge>
				<Badge>SG_RESULT: {live.tmc_sg_result ?? '--'}</Badge>
				<Badge tone={drvStatusSummary(live.tmc_drv_status) === $_('routes.settings.octopusDiagnostic.live.clean') ? 'neutral' : 'danger'}>
					DRV_STATUS: {drvStatusSummary(live.tmc_drv_status)}
				</Badge>
				{#if live.encoder_error}<Badge tone="danger">{live.encoder_error}</Badge>{/if}
			</div>
		{/if}
	</Panel>

	<Panel
		title={$_('routes.settings.octopusDiagnostic.binWheel.panelTitle')}
		description={$_('routes.settings.octopusDiagnostic.binWheel.panelDescription')}
	>
		<div class="flex flex-col gap-3 px-(--pad-panel) pb-(--pad-panel)">
			<Field label={$_('routes.settings.octopusDiagnostic.binWheel.layerLabel')} for="diag-layer">
				<Select
					id="diag-layer"
					value={String(layerIndex)}
					onchange={(v) => (layerIndex = Number(v))}
					options={layers.map((l) => ({
						value: String(l.layer_index),
						label: $_('routes.settings.octopusDiagnostic.binWheel.layerOption', { values: { n: l.layer_index + 1 } })
					}))}
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

	{#if live?.chute_position_sensor === 'encoder_native'}
		<Panel
			title={$_('routes.settings.octopusDiagnostic.homeCorrection.panelTitle')}
			description={$_('routes.settings.octopusDiagnostic.homeCorrection.panelDescription')}
		>
			<div class="flex flex-wrap items-end gap-3 px-(--pad-panel) pb-(--pad-panel)">
				<Field
					label={$_('routes.settings.octopusDiagnostic.homeCorrection.label')}
					for="diag-home-correction"
				>
					<Input
						id="diag-home-correction"
						type="number"
						step={1}
						value={homeCorrectionInput}
						disabled={homeCorrectionSaving}
						oninput={(e) => (homeCorrectionInput = (e.currentTarget as HTMLInputElement).value)}
						class="w-28"
					/>
				</Field>
				<Button
					variant="primary"
					loading={homeCorrectionSaving}
					disabled={homeCorrectionInput.trim() === String(homeCorrectionUsteps ?? '')}
					onclick={() => void saveHomeCorrection()}
				>
					{$_('routes.settings.octopusDiagnostic.homeCorrection.saveButton')}
				</Button>
			</div>
		</Panel>
	{/if}
{:else if tab === 'steppers'}
	<div class="flex flex-col gap-(--gap-panels)">
		<StepperPanel stepperKey="c_channel_1" keyboardShortcuts />
		<StepperPanel stepperKey="c_channel_2" keyboardShortcuts />
		<StepperPanel stepperKey="c_channel_3" keyboardShortcuts />
		<StepperPanel stepperKey="carousel" keyboardShortcuts />
	</div>
{:else if tab === 'leds'}
	<div class="flex flex-col gap-(--gap-panels)">
		<Panel
			title={$_('routes.settings.octopusDiagnostic.leds.cChannel2.title')}
			description={$_('routes.settings.octopusDiagnostic.leds.cChannel2.description')}
		>
			<div class="px-(--pad-panel) pb-(--pad-panel)"><ChannelLedSection channelKey="c_channel_2" /></div>
		</Panel>
		<Panel
			title={$_('routes.settings.octopusDiagnostic.leds.cChannel3.title')}
			description={$_('routes.settings.octopusDiagnostic.leds.cChannel3.description')}
		>
			<div class="px-(--pad-panel) pb-(--pad-panel)"><ChannelLedSection channelKey="c_channel_3" /></div>
		</Panel>
		<Panel
			title={$_('routes.settings.octopusDiagnostic.leds.classificationChannel.title')}
			description={$_('routes.settings.octopusDiagnostic.leds.classificationChannel.description')}
		>
			<div class="px-(--pad-panel) pb-(--pad-panel)"><ChannelLedSection channelKey="classification_channel" /></div>
		</Panel>
	</div>
{:else if tab === 'fans'}
	<Panel
		title={$_('routes.settings.octopusDiagnostic.fans.cpuPanelTitle')}
		description={$_('routes.settings.octopusDiagnostic.fans.cpuPanelDescription', {
			values: {
				autoFanIndex: cpu?.auto_fan_index ?? 5,
				fanOnTemp: cpu?.fan_on_at_or_above_c ?? 55,
				fanOffTemp: cpu?.fan_off_below_c ?? 50,
				minSwitchInterval: cpu?.min_switch_interval_s ?? 60,
				throttleTemp: cpu?.throttle_c ?? 85
			}
		})}
	>
		<div class="grid grid-cols-2 divide-x divide-line sm:grid-cols-4">
			<Stat
				label={$_('routes.settings.octopusDiagnostic.fans.cpuTempLabel')}
				value={cpu?.temp_c ?? '--'}
				unit={cpu?.temp_c != null ? '°C' : undefined}
				tone={cpu?.temp_c != null && cpu.fan_on_at_or_above_c != null && cpu.temp_c >= cpu.fan_on_at_or_above_c
					? 'warning'
					: undefined}
			/>
			<Stat
				label="FAN{cpu?.auto_fan_index ?? 5}"
				value={cpu?.fan_on === null || cpu?.fan_on === undefined
					? '--'
					: cpu.fan_on
						? $_('routes.settings.octopusDiagnostic.fans.on')
						: $_('routes.settings.octopusDiagnostic.fans.off')}
			/>
			<Stat label={$_('routes.settings.octopusDiagnostic.fans.onOffLabel')} value={cpu ? `${cpu.fan_on_at_or_above_c} / ${cpu.fan_off_below_c}` : '--'} unit="°C" />
			<Stat label={$_('routes.settings.octopusDiagnostic.fans.minSwitchLabel')} value={cpu?.min_switch_interval_s ?? '--'} unit="s" />
		</div>
	</Panel>
	<Panel
		title={$_('routes.settings.octopusDiagnostic.fans.headersPanelTitle')}
		description={$_('routes.settings.octopusDiagnostic.fans.headersPanelDescription')}
	>
		{#if fansLoading}
			<div class="px-(--pad-panel) pb-(--pad-panel) text-sm text-ink-muted">{$_('routes.settings.octopusDiagnostic.fans.loading')}</div>
		{:else if !fansAvailable}
			<div class="px-(--pad-panel) pb-(--pad-panel) text-sm text-ink-muted">
				{$_('routes.settings.octopusDiagnostic.fans.notAvailable')}
			</div>
		{:else}
			<div class="grid grid-cols-1 gap-4 px-(--pad-panel) pb-(--pad-panel) sm:grid-cols-2 lg:grid-cols-3">
				{#each fans as fan (fan.index)}
					<div class="flex flex-col gap-2 rounded-control bg-well p-3">
						<div class="flex items-center justify-between text-sm">
							<span class="font-medium text-ink">FAN{fan.index}</span>
							{#if fan.auto}
								<Badge tone={fan.duty_percent > 0 ? 'success' : undefined}>{$_('routes.settings.octopusDiagnostic.fans.auto')}</Badge>
							{:else}
								<span class="num text-ink-muted">{fan.duty_percent}%</span>
							{/if}
						</div>
						{#if fan.auto}
							<div class="text-sm text-ink-muted">
								{$_('routes.settings.octopusDiagnostic.fans.controlledByCpu', {
									values: {
										state: fan.duty_percent > 0
											? $_('routes.settings.octopusDiagnostic.fans.currentlyOn')
											: $_('routes.settings.octopusDiagnostic.fans.currentlyOff')
									}
								})}
							</div>
						{:else}
							<input
								type="range"
								min="0"
								max="100"
								step="1"
								value={fan.duty_percent}
								disabled={savingFan === fan.index}
								oninput={(e) => (fan.duty_percent = Number(e.currentTarget.value))}
								onchange={(e) => void setFan(fan.index, Number(e.currentTarget.value))}
								class="accent-primary"
							/>
							<div class="flex gap-2">
								<Button size="sm" disabled={savingFan === fan.index} onclick={() => void setFan(fan.index, 0)}>
									{$_('routes.settings.octopusDiagnostic.fans.off')}
								</Button>
								<Button size="sm" disabled={savingFan === fan.index} onclick={() => void setFan(fan.index, 100)}>
									{$_('routes.settings.octopusDiagnostic.fans.full')}
								</Button>
							</div>
						{/if}
					</div>
				{/each}
			</div>
		{/if}
	</Panel>
{:else if tab === 'benchmark'}
	<BenchmarkPanel />
{/if}
