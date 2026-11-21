<script lang="ts">
	import { _ } from 'svelte-i18n';
	import { getBackendHttpBase, machineHttpBaseUrlFromWsUrl } from '$lib/backend';
	import { getMachinesContext } from '$lib/machines/context';
	import Panel from '$lib/components/ui/Panel.svelte';
	import Badge from '$lib/components/ui/Badge.svelte';
	import Button from '$lib/components/ui/Button.svelte';
	import Select from '$lib/components/ui/Select.svelte';
	import Input from '$lib/components/ui/Input.svelte';
	import Checkbox from '$lib/components/ui/Checkbox.svelte';
	import Field from '$lib/components/ui/Field.svelte';
	import Spinner from '$lib/components/ui/Spinner.svelte';
	import Alert from '$lib/components/ui/Alert.svelte';
	import ChevronDown from '@lucide/svelte/icons/chevron-down';
	import ChevronUp from '@lucide/svelte/icons/chevron-up';
	import Play from '@lucide/svelte/icons/play';
	import Pause from '@lucide/svelte/icons/pause';
	import Square from '@lucide/svelte/icons/square';
	import { onDestroy, onMount, untrack } from 'svelte';
	import ChuteStressTelemetryChart from './ChuteStressTelemetryChart.svelte';

	type StressMode = 'sweep' | 'random' | 'acceleration_ramp';
	type RunStatus =
		| 'running'
		| 'paused'
		| 'stopping'
		| 'completed'
		| 'stopped'
		| 'failed'
		| 'stalled';

	type RunRecord = {
		id: string;
		started_at: number;
		ended_at: number | null;
		mode: StressMode;
		target_max_deg: number;
		duration_target_s: number;
		speed_microsteps_per_sec: number;
		status: RunStatus;
		total_distance_deg: number;
		total_time_s: number;
		error: string | null;
		last_target_deg?: number | null;
		stalled_at_deg?: number | null;
		current_acceleration?: number | null;
		highest_clean_acceleration?: number | null;
	};

	const CHUTE_STRESS_MAX_ANGLE = 345;

	let {
		operatingSpeed
	}: {
		operatingSpeed: number;
	} = $props();

	const manager = getMachinesContext();

	let mode = $state<StressMode>('sweep');
	let targetMaxDeg = $state(340);
	let durationSec = $state(60);
	let useMaxSpeed = $state(true);
	let speedOverride = $state(operatingSpeed);
	let invertDirection = $state(false);
	let accelStart = $state(2000);
	let accelStep = $state(1000);
	let accelMovesPerStep = $state(10);

	let errorMsg = $state<string | null>(null);
	let statusMsg = $state('');
	let activeRun = $state<RunRecord | null>(null);
	let active = $state(false);
	let busy = $state(false);

	let runs = $state<RunRecord[]>([]);
	// The latest few runs; the rest on request, so the list never scrolls inside the page.
	const RUNS_SHOWN = 6;
	let showAllRuns = $state(false);
	const shownRuns = $derived(showAllRuns ? runs : runs.slice(0, RUNS_SHOWN));
	let selectedRunId = $state<string | null>(null);
	let pollHandle: ReturnType<typeof setInterval> | null = null;

	const effectiveSpeed = $derived(
		useMaxSpeed ? Math.max(1, Math.floor(operatingSpeed)) : Math.max(1, Math.floor(speedOverride))
	);

	$effect(() => {
		if (useMaxSpeed) speedOverride = operatingSpeed;
	});

	function backendBase(): string {
		return (
			machineHttpBaseUrlFromWsUrl(
				manager.selectedMachine?.status === 'connected' ? manager.selectedMachine.url : null
			) ?? getBackendHttpBase()
		);
	}

	async function readError(res: Response): Promise<string> {
		try {
			const data = await res.json();
			if (typeof data?.detail === 'string') return data.detail;
		} catch {
			/* fall through */
		}
		try {
			return await res.text();
		} catch {
			return `Request failed with status ${res.status}`;
		}
	}

	async function loadStatus() {
		try {
			const res = await fetch(`${backendBase()}/api/chute/stress-test/status`);
			if (!res.ok) return;
			const data = await res.json();
			active = Boolean(data?.active);
			activeRun = data?.run ?? null;
		} catch {
			// silent
		}
	}

	async function loadRuns() {
		try {
			const res = await fetch(`${backendBase()}/api/chute/stress-test/runs?limit=50`);
			if (!res.ok) return;
			const data = await res.json();
			if (Array.isArray(data?.runs)) runs = data.runs;
		} catch {
			// silent
		}
	}

	async function postAction(path: string, body?: unknown): Promise<boolean> {
		busy = true;
		errorMsg = null;
		statusMsg = '';
		try {
			const res = await fetch(`${backendBase()}${path}`, {
				method: 'POST',
				headers: body ? { 'Content-Type': 'application/json' } : undefined,
				body: body ? JSON.stringify(body) : undefined
			});
			if (!res.ok) {
				errorMsg = await readError(res);
				return false;
			}
			const data = await res.json();
			active = Boolean(data?.active);
			activeRun = data?.run ?? null;
			return true;
		} catch (e: any) {
			errorMsg =
				e?.message ?? $_('components.settings.stepper.stepperChuteStressTest.errors.requestFailed');
			return false;
		} finally {
			busy = false;
		}
	}

	async function startRun() {
		if (mode !== 'acceleration_ramp' && (targetMaxDeg <= 0 || targetMaxDeg > CHUTE_STRESS_MAX_ANGLE)) {
			errorMsg = $_('components.settings.stepper.stepperChuteStressTest.errors.targetRange', {
				values: { max: CHUTE_STRESS_MAX_ANGLE }
			});
			return;
		}
		if (durationSec <= 0) {
			errorMsg = $_('components.settings.stepper.stepperChuteStressTest.errors.durationPositive');
			return;
		}
		const ok = await postAction('/api/chute/stress-test/start', {
			mode,
			target_max_deg: targetMaxDeg,
			duration_s: durationSec,
			speed_microsteps_per_sec: effectiveSpeed,
			invert_direction: invertDirection,
			...(mode === 'acceleration_ramp'
				? {
						accel_start_microsteps_per_second_sq: accelStart,
						accel_step_microsteps_per_second_sq: accelStep,
						accel_moves_per_step: accelMovesPerStep
					}
				: {})
		});
		if (ok) {
			statusMsg = $_('components.settings.stepper.stepperChuteStressTest.status.started');
			await loadRuns();
		}
	}

	async function pauseRun() {
		const ok = await postAction('/api/chute/stress-test/pause');
		if (ok)
			statusMsg = $_('components.settings.stepper.stepperChuteStressTest.status.pauseRequested');
	}

	async function resumeRun() {
		const ok = await postAction('/api/chute/stress-test/resume');
		if (ok) statusMsg = $_('components.settings.stepper.stepperChuteStressTest.status.resumed');
	}

	async function stopRun() {
		const ok = await postAction('/api/chute/stress-test/stop');
		if (ok) {
			statusMsg = $_('components.settings.stepper.stepperChuteStressTest.status.stopRequested');
			await loadRuns();
		}
	}

	function formatDuration(seconds: number): string {
		if (!Number.isFinite(seconds) || seconds < 0) return '--';
		const total = Math.floor(seconds);
		const h = Math.floor(total / 3600);
		const m = Math.floor((total % 3600) / 60);
		const s = total % 60;
		if (h > 0) return `${h}h ${m}m ${s}s`;
		if (m > 0) return `${m}m ${s}s`;
		return `${s}s`;
	}

	function formatTimestamp(epoch_s: number | null | undefined): string {
		if (!epoch_s || !Number.isFinite(epoch_s)) return '--';
		try {
			return new Date(epoch_s * 1000).toLocaleString();
		} catch {
			return '--';
		}
	}

	function statusLabel(status: RunStatus): string {
		const base = 'components.settings.stepper.stepperChuteStressTest.runStatus';
		switch (status) {
			case 'running':
				return $_(`${base}.running`);
			case 'paused':
				return $_(`${base}.paused`);
			case 'stopping':
				return $_(`${base}.stopping`);
			case 'completed':
				return $_(`${base}.completed`);
			case 'stopped':
				return $_(`${base}.stopped`);
			case 'failed':
				return $_(`${base}.failed`);
			case 'stalled':
				return $_(`${base}.stalled`);
			default:
				return status;
		}
	}

	function statusTone(status: RunStatus): 'primary' | 'warning' | 'success' | 'danger' | 'neutral' {
		if (status === 'running') return 'primary';
		if (status === 'paused' || status === 'stopping') return 'warning';
		if (status === 'completed') return 'success';
		if (status === 'failed' || status === 'stalled') return 'danger';
		return 'neutral';
	}

	function startPolling() {
		if (pollHandle !== null) return;
		pollHandle = setInterval(() => {
			void loadStatus();
		}, 500);
	}

	function stopPolling() {
		if (pollHandle !== null) {
			clearInterval(pollHandle);
			pollHandle = null;
		}
	}

	$effect(() => {
		if (active) startPolling();
		else stopPolling();
	});

	$effect(() => {
		// When the active run transitions away from running/paused, refresh the runs list.
		// untrack the body so this only depends on the status string — not on the
		// machines context that loadRuns()/backendBase() read, which would otherwise
		// re-fire this effect on every connection heartbeat.
		const status = activeRun?.status;
		const endedId = activeRun?.id;
		const stallMsg = activeRun?.error;
		untrack(() => {
			if (status === 'stalled') {
				errorMsg =
					stallMsg ?? $_('components.settings.stepper.stepperChuteStressTest.errors.stalled');
			}
			if (
				status === 'completed' ||
				status === 'stopped' ||
				status === 'failed' ||
				status === 'stalled'
			) {
				void loadRuns();
				// One final silent refresh so the last flushed samples land after the
				// live poller stops (it stops the moment the run goes inactive).
				if (endedId && selectedRunId === endedId) void loadTelemetry(endedId, true);
			}
		});
	});

	// The current run and the past ones, when the page opens.
	onMount(() => {
		void loadStatus();
		void loadRuns();
	});

	onDestroy(() => {
		stopPolling();
		stopTelemetryPolling();
	});

	const selectedRun = $derived(
		selectedRunId ? runs.find((r) => r.id === selectedRunId) ?? null : null
	);

	type TelemetryPoint = {
		x: number;
		sg: number | null;
		cs: number | null;
		pwm: number | null;
		tstep: number | null;
		warn: boolean;
	};

	type DriverSettings = {
		registers?: Record<string, number | null>;
		decoded?: Record<string, Record<string, unknown>>;
		configured?: Record<string, unknown>;
	};

	let telemetryPoints = $state<TelemetryPoint[]>([]);
	let telemetrySettings = $state<DriverSettings | null>(null);
	let telemetryRunId = $state<string | null>(null);
	let telemetryLoading = $state(false);
	let telemetryError = $state<string | null>(null);
	let showSg = $state(true);
	let showCs = $state(true);
	let showPwm = $state(true);
	let showTstep = $state(false);

	function numOrNull(v: unknown): number | null {
		return typeof v === 'number' && Number.isFinite(v) ? v : null;
	}

	function sampleWarn(drv: number | null): boolean {
		if (drv == null) return false;
		// otpw (bit0) or any over-temperature threshold flag (t120..t157, bits 8..11)
		return (drv & ((1 << 0) | (1 << 8) | (1 << 9) | (1 << 10) | (1 << 11))) !== 0;
	}

	function seriesStat(key: 'sg' | 'cs' | 'pwm' | 'tstep'): {
		min: number;
		max: number;
		last: number;
	} | null {
		const vals = telemetryPoints
			.map((p) => p[key])
			.filter((v): v is number => v != null && v >= 0);
		if (vals.length === 0) return null;
		return { min: Math.min(...vals), max: Math.max(...vals), last: vals[vals.length - 1] };
	}

	const sgStat = $derived(seriesStat('sg'));
	const csStat = $derived(seriesStat('cs'));
	const pwmStat = $derived(seriesStat('pwm'));
	const tstepStat = $derived(seriesStat('tstep'));
	const warnCount = $derived(telemetryPoints.filter((p) => p.warn).length);

	function hex32(v: number | null | undefined): string {
		if (v == null) return '--';
		return `0x${(v >>> 0).toString(16).toUpperCase().padStart(8, '0')}`;
	}

	function decodedField(group: string, field: string): unknown {
		return telemetrySettings?.decoded?.[group]?.[field];
	}

	function configuredCurrent(): Record<string, unknown> | null {
		const c = telemetrySettings?.configured?.['last_set_current'];
		return c && typeof c === 'object' ? (c as Record<string, unknown>) : null;
	}

	function snapshotTempBand(): string {
		const drv = telemetrySettings?.decoded?.['drv_status'];
		if (!drv) return '--';
		if (drv['t157']) return '≥157°C';
		if (drv['t150']) return '≥150°C';
		if (drv['t143']) return '≥143°C';
		if (drv['t120']) return '≥120°C';
		return '<120°C';
	}

	async function loadTelemetry(runId: string, silent = false) {
		// silent=true is used by the live poller during an active run: it updates the
		// data in place and never touches loading/error state or clears the panel, so
		// the UI can't swap blocks (which is what caused the height jitter).
		if (!silent) {
			telemetryLoading = true;
			telemetryError = null;
		}
		try {
			const res = await fetch(`${backendBase()}/api/chute/stress-test/runs/${runId}/telemetry`);
			if (res.status === 404) {
				if (!silent) {
					telemetryPoints = [];
					telemetrySettings = null;
					telemetryRunId = null;
					telemetryError = $_(
						'components.settings.stepper.stepperChuteStressTest.telemetry.noTelemetryRecorded'
					);
				}
				return;
			}
			if (!res.ok) {
				if (!silent) telemetryError = await readError(res);
				return;
			}
			const data = await res.json();
			const run = data?.run ?? null;
			telemetrySettings = (run?.params ?? null) as DriverSettings | null;
			telemetryRunId = run?.id ?? null;
			const rows: any[] = Array.isArray(data?.samples) ? data.samples : [];
			const t0 = rows.length ? Number(rows[0].recorded_at) : 0;
			telemetryPoints = rows.map((r) => ({
				x: Number(r.recorded_at) - t0,
				sg: numOrNull(r.sg_result),
				cs: numOrNull(r.cs_actual),
				pwm: r.pwm_scale != null ? (Number(r.pwm_scale) & 0xff) : null,
				tstep: numOrNull(r.tstep),
				warn: sampleWarn(r.drv_status_raw != null ? Number(r.drv_status_raw) : null)
			}));
		} catch (e: any) {
			if (!silent) telemetryError = e?.message ?? 'Failed to load telemetry';
		} finally {
			if (!silent) telemetryLoading = false;
		}
	}

	let lastTelemetryId: string | null = null;
	$effect(() => {
		// Depend ONLY on selectedRunId. untrack the body so loadTelemetry()'s call to
		// backendBase() (which reads the machines context) doesn't make this effect
		// re-fire on every context update and re-request telemetry in a loop.
		const id = selectedRunId;
		untrack(() => {
			if (id) {
				if (id !== lastTelemetryId) {
					lastTelemetryId = id;
					void loadTelemetry(id);
				}
			} else {
				lastTelemetryId = null;
				telemetryPoints = [];
				telemetrySettings = null;
				telemetryRunId = null;
				telemetryError = null;
			}
		});
	});

	// True when the run currently being viewed is the one actively running.
	const telemetryLive = $derived(active && activeRun != null && selectedRunId === activeRun.id);

	// While a run is active, point the telemetry view at it (unless the user has
	// explicitly selected a different past run to inspect).
	$effect(() => {
		const activeId = active ? activeRun?.id : null;
		untrack(() => {
			if (activeId && !selectedRunId) selectedRunId = activeId;
		});
	});

	let telemetryPollHandle: ReturnType<typeof setInterval> | null = null;
	function startTelemetryPolling() {
		if (telemetryPollHandle !== null) return;
		// Matches the backend recorder's ~2s DB flush. Silent refresh = update in
		// place, no block swaps, no jitter.
		telemetryPollHandle = setInterval(() => {
			const id = selectedRunId;
			if (id) void loadTelemetry(id, true);
		}, 1500);
	}
	function stopTelemetryPolling() {
		if (telemetryPollHandle !== null) {
			clearInterval(telemetryPollHandle);
			telemetryPollHandle = null;
		}
	}

	$effect(() => {
		if (telemetryLive) startTelemetryPolling();
		else stopTelemetryPolling();
	});
</script>

{#snippet facts(rows: [string, string, string?][])}
	<dl class="grid grid-cols-2 gap-x-4 gap-y-1 text-sm">
		{#each rows as [label, value, cls]}
			<dt class="text-ink-muted">{label}</dt>
			<dd class="num text-right {cls ?? 'text-ink'}">{value}</dd>
		{/each}
	</dl>
{/snippet}

{#snippet swatch(color: string)}
	<span class="inline-block size-2 shrink-0 {color}" aria-hidden="true"></span>
{/snippet}

<Panel
	title={$_('components.settings.stepper.stepperChuteStressTest.panel.stressTest.title')}
	description={$_('components.settings.stepper.stepperChuteStressTest.panel.stressTest.description')}
>
	<div class="flex flex-col gap-6">
		{#if active && activeRun}
			<div class="rounded-control bg-well p-3">
				<div class="mb-2 flex items-center justify-between gap-2">
					<Badge tone={statusTone(activeRun.status)} dot>{statusLabel(activeRun.status)}</Badge>
					<span class="text-sm text-ink-muted">{activeRun.mode}</span>
				</div>
				{@render facts([
					[
						$_('components.settings.stepper.stepperChuteStressTest.facts.elapsed'),
						$_('components.settings.stepper.stepperChuteStressTest.facts.elapsedValue', {
							values: {
								elapsed: formatDuration(activeRun.total_time_s),
								total: formatDuration(activeRun.duration_target_s)
							}
						})
					],
					[
						$_('components.settings.stepper.stepperChuteStressTest.facts.distance'),
						`${activeRun.total_distance_deg.toFixed(1)}°`
					],
					[
						$_('components.settings.stepper.stepperChuteStressTest.facts.lastTarget'),
						activeRun.last_target_deg != null
							? `${activeRun.last_target_deg.toFixed(1)}°`
							: $_('components.settings.stepper.stepperChuteStressTest.facts.none')
					],
					[
						$_('components.settings.stepper.stepperChuteStressTest.facts.speed'),
						`${activeRun.speed_microsteps_per_sec} µsteps/s`
					],
					...(activeRun.mode === 'acceleration_ramp'
						? [
								[
									$_('components.settings.stepper.stepperChuteStressTest.facts.currentAcceleration'),
									`${activeRun.current_acceleration ?? '?'} µsteps/s²`
								] as [string, string],
								[
									$_('components.settings.stepper.stepperChuteStressTest.facts.highestClean'),
									activeRun.highest_clean_acceleration != null
										? `${activeRun.highest_clean_acceleration} µsteps/s²`
										: $_('components.settings.stepper.stepperChuteStressTest.facts.noneYet')
								] as [string, string]
							]
						: [])
				])}
			</div>
		{/if}

		<div class="flex flex-col gap-4">
			<div class="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-3">
				<Field
					label={$_('components.settings.stepper.stepperChuteStressTest.field.pattern')}
					for="stress-mode"
				>
					<Select
						id="stress-mode"
						bind:value={mode}
						disabled={active || busy}
						options={[
							{
								value: 'sweep',
								label: $_('components.settings.stepper.stepperChuteStressTest.mode.sweep')
							},
							{
								value: 'random',
								label: $_('components.settings.stepper.stepperChuteStressTest.mode.random')
							},
							{
								value: 'acceleration_ramp',
								label: $_(
									'components.settings.stepper.stepperChuteStressTest.mode.accelerationRamp'
								)
							}
						]}
					/>
				</Field>
				{#if mode !== 'acceleration_ramp'}
					<Field
						label={$_('components.settings.stepper.stepperChuteStressTest.field.target')}
						for="stress-target"
						help={$_('components.settings.stepper.stepperChuteStressTest.field.targetHelp', {
							values: { max: CHUTE_STRESS_MAX_ANGLE }
						})}
					>
						<Input
							id="stress-target"
							type="number"
							min={1}
							max={CHUTE_STRESS_MAX_ANGLE}
							step={1}
							bind:value={targetMaxDeg}
							disabled={active || busy}
							unit="°"
						/>
					</Field>
				{/if}
				<Field
					label={$_('components.settings.stepper.stepperChuteStressTest.field.duration')}
					for="stress-duration"
				>
					<Input id="stress-duration" type="number" min={1} step={1} bind:value={durationSec} disabled={active || busy} unit="s" />
				</Field>
				{#if !useMaxSpeed}
					<Field
						label={$_('components.settings.stepper.stepperChuteStressTest.field.speed')}
						for="stress-speed"
					>
						<Input
							id="stress-speed"
							type="number"
							min={1}
							step={100}
							bind:value={speedOverride}
							disabled={active || busy}
							unit="µsteps/s"
						/>
					</Field>
				{/if}
				{#if mode === 'acceleration_ramp'}
					<Field
						label={$_('components.settings.stepper.stepperChuteStressTest.field.startAcceleration')}
						for="stress-accel-start"
					>
						<Input
							id="stress-accel-start"
							type="number"
							min={1}
							step={100}
							bind:value={accelStart}
							disabled={active || busy}
							unit="µsteps/s²"
						/>
					</Field>
					<Field
						label={$_('components.settings.stepper.stepperChuteStressTest.field.step')}
						for="stress-accel-step"
					>
						<Input
							id="stress-accel-step"
							type="number"
							min={1}
							step={100}
							bind:value={accelStep}
							disabled={active || busy}
							unit="µsteps/s²"
						/>
					</Field>
					<Field
						label={$_('components.settings.stepper.stepperChuteStressTest.field.movesPerStep')}
						for="stress-accel-moves"
					>
						<Input
							id="stress-accel-moves"
							type="number"
							min={1}
							step={1}
							bind:value={accelMovesPerStep}
							disabled={active || busy}
						/>
					</Field>
				{/if}
			</div>
			<div class="flex flex-wrap gap-x-6 gap-y-2">
				<Checkbox bind:checked={useMaxSpeed} disabled={active || busy}>
					{$_('components.settings.stepper.stepperChuteStressTest.checkbox.useOperatingSpeed', {
						values: { speed: operatingSpeed }
					})}
				</Checkbox>
				{#if mode !== 'acceleration_ramp'}
					<Checkbox bind:checked={invertDirection} disabled={active || busy}
						>{$_('components.settings.stepper.stepperChuteStressTest.checkbox.invertDirection')}</Checkbox
					>
				{/if}
			</div>
		</div>

		{#if errorMsg}
			<Alert tone="danger">{errorMsg}</Alert>
		{/if}
	</div>
	{#snippet footer()}
		{#if statusMsg && !errorMsg}
			<p class="mr-auto min-w-0 text-sm text-ink-muted">{statusMsg}</p>
		{/if}
		{#if !active}
			<Button variant="primary" icon={Play} loading={busy} onclick={startRun}
				>{$_('components.settings.stepper.stepperChuteStressTest.buttons.startStressTest')}</Button
			>
		{:else}
			{#if activeRun?.status === 'paused'}
				<Button icon={Play} loading={busy} onclick={resumeRun}
					>{$_('components.settings.stepper.stepperChuteStressTest.buttons.resume')}</Button
				>
			{:else}
				<Button icon={Pause} loading={busy} disabled={activeRun?.status === 'stopping'} onclick={pauseRun}>
					{$_('components.settings.stepper.stepperChuteStressTest.buttons.pause')}
				</Button>
			{/if}
			<Button variant="danger" icon={Square} disabled={busy} onclick={stopRun}
				>{$_('components.settings.stepper.stepperChuteStressTest.buttons.stop')}</Button
			>
		{/if}
	{/snippet}
</Panel>

<Panel
	title={$_('components.settings.stepper.stepperChuteStressTest.panel.runs.title')}
	description={$_('components.settings.stepper.stepperChuteStressTest.panel.runs.description')}
>
	{#snippet actions()}
		<Button variant="ghost" size="sm" onclick={() => void loadRuns()}
			>{$_('components.settings.stepper.stepperChuteStressTest.buttons.refresh')}</Button
		>
	{/snippet}
	<div class="flex flex-col gap-4">
		{#if runs.length === 0}
			<p class="text-sm text-ink-muted">
				{$_('components.settings.stepper.stepperChuteStressTest.runsList.empty')}
			</p>
		{:else}
			<ul class="divide-y divide-line overflow-hidden rounded-control bg-well">
				{#each shownRuns as run (run.id)}
					<li>
						<button
							type="button"
							aria-pressed={selectedRunId === run.id}
							onclick={() => (selectedRunId = selectedRunId === run.id ? null : run.id)}
							class="flex w-full items-center justify-between gap-2 px-3 py-2 text-left text-sm transition-colors
								{selectedRunId === run.id ? 'bg-primary-soft' : 'hover:bg-hover'}"
						>
							<span class="min-w-0 truncate text-ink">{formatTimestamp(run.started_at)}</span>
							<Badge tone={statusTone(run.status)}>{statusLabel(run.status)}</Badge>
						</button>
					</li>
				{/each}
			</ul>
			{#if runs.length > RUNS_SHOWN}
				<div>
					<Button
						variant="ghost"
						size="sm"
						icon={showAllRuns ? ChevronUp : ChevronDown}
						onclick={() => (showAllRuns = !showAllRuns)}
					>
						{showAllRuns
							? $_('components.settings.stepper.stepperChuteStressTest.runsList.showLatest', {
									values: { n: RUNS_SHOWN }
								})
							: $_('components.settings.stepper.stepperChuteStressTest.runsList.showAll', {
									values: { n: runs.length }
								})}
					</Button>
				</div>
			{/if}
		{/if}

		{#if selectedRun}
			<div class="rounded-control bg-well p-3">
				{@render facts([
					[$_('components.settings.stepper.stepperChuteStressTest.facts.started'), formatTimestamp(selectedRun.started_at)],
					[$_('components.settings.stepper.stepperChuteStressTest.facts.ended'), formatTimestamp(selectedRun.ended_at)],
					[$_('components.settings.stepper.stepperChuteStressTest.facts.pattern'), selectedRun.mode],
					[$_('components.settings.stepper.stepperChuteStressTest.facts.target'), `${selectedRun.target_max_deg.toFixed(1)}°`],
					[$_('components.settings.stepper.stepperChuteStressTest.facts.planned'), formatDuration(selectedRun.duration_target_s)],
					[$_('components.settings.stepper.stepperChuteStressTest.facts.ran'), formatDuration(selectedRun.total_time_s)],
					[$_('components.settings.stepper.stepperChuteStressTest.facts.distance'), `${selectedRun.total_distance_deg.toFixed(1)}°`],
					[$_('components.settings.stepper.stepperChuteStressTest.facts.speed'), `${selectedRun.speed_microsteps_per_sec} µsteps/s`],
					...(selectedRun.highest_clean_acceleration != null
						? [[$_('components.settings.stepper.stepperChuteStressTest.facts.highestCleanAcceleration'), `${selectedRun.highest_clean_acceleration} µsteps/s²`] as [string, string]]
						: [])
				])}
				{#if selectedRun.error}
					<p class="mt-2 text-sm text-danger-ink">{selectedRun.error}</p>
				{/if}
			</div>
		{/if}

		{#if selectedRunId}
			<div class="flex flex-col gap-3 border-t border-line pt-4">
				<div class="flex items-center justify-between gap-2">
					<div class="flex items-center gap-2">
						<span class="label"
							>{$_('components.settings.stepper.stepperChuteStressTest.telemetry.driverReadings')}</span
						>
						{#if telemetryLive}<Badge tone="success" dot
								>{$_('components.settings.stepper.stepperChuteStressTest.telemetry.live')}</Badge
							>{/if}
					</div>
					<Button variant="ghost" size="sm" onclick={() => selectedRunId && void loadTelemetry(selectedRunId)}>
						{$_('components.settings.stepper.stepperChuteStressTest.buttons.refresh')}
					</Button>
				</div>

				{#if telemetryLoading}
					<p class="flex items-center gap-2 text-sm text-ink-muted">
						<Spinner size={14} />
						{$_('components.settings.stepper.stepperChuteStressTest.telemetry.loadingReadings')}
					</p>
				{:else if telemetryError}
					<p class="text-sm text-ink-muted">{telemetryError}</p>
				{:else if telemetryPoints.length === 0}
					<p class="text-sm text-ink-muted">
						{$_('components.settings.stepper.stepperChuteStressTest.telemetry.noReadingsForRun')}
					</p>
				{:else}
					{#if telemetrySettings}
						<div class="rounded-control bg-well p-3">
							<div class="label mb-2">
								{$_(
									'components.settings.stepper.stepperChuteStressTest.telemetry.driverSettingsHeading'
								)}
							</div>
							{@render facts([
								[
									$_('components.settings.stepper.stepperChuteStressTest.telemetry.chopperMode'),
									decodedField('gconf', 'stealthchop') === undefined
										? $_('components.settings.stepper.stepperChuteStressTest.telemetry.unknown')
										: decodedField('gconf', 'stealthchop')
											? 'StealthChop'
											: 'SpreadCycle',
									decodedField('gconf', 'stealthchop') ? 'text-warning-ink' : undefined
								],
								[
									$_('components.settings.stepper.stepperChuteStressTest.telemetry.microsteps'),
									String(
										decodedField('chopconf', 'microsteps') ??
											$_('components.settings.stepper.stepperChuteStressTest.telemetry.unknown')
									)
								],
								[
									$_('components.settings.stepper.stepperChuteStressTest.telemetry.interpolation'),
									decodedField('chopconf', 'intpol') === undefined
										? $_('components.settings.stepper.stepperChuteStressTest.telemetry.unknown')
										: decodedField('chopconf', 'intpol')
											? $_('components.settings.stepper.stepperChuteStressTest.telemetry.on')
											: $_('components.settings.stepper.stepperChuteStressTest.telemetry.off')
								],
								[
									$_('components.settings.stepper.stepperChuteStressTest.telemetry.runCurrent'),
									$_('components.settings.stepper.stepperChuteStressTest.telemetry.ofMax31', {
										values: { val: configuredCurrent()?.['irun'] ?? '?' }
									})
								],
								[
									$_('components.settings.stepper.stepperChuteStressTest.telemetry.holdCurrent'),
									$_('components.settings.stepper.stepperChuteStressTest.telemetry.ofMax31', {
										values: { val: configuredCurrent()?.['ihold'] ?? '?' }
									})
								],
								[
									$_('components.settings.stepper.stepperChuteStressTest.telemetry.csActualAtStart'),
									$_('components.settings.stepper.stepperChuteStressTest.telemetry.ofMax31', {
										values: { val: decodedField('drv_status', 'cs_actual') ?? '?' }
									})
								],
								[
									$_(
										'components.settings.stepper.stepperChuteStressTest.telemetry.heatWarningAtStart'
									),
									decodedField('drv_status', 'otpw') === undefined
										? $_('components.settings.stepper.stepperChuteStressTest.telemetry.unknown')
										: decodedField('drv_status', 'otpw')
											? $_('components.settings.stepper.stepperChuteStressTest.telemetry.yes')
											: $_('components.settings.stepper.stepperChuteStressTest.telemetry.no'),
									decodedField('drv_status', 'otpw') ? 'text-danger-ink' : undefined
								],
								[
									$_('components.settings.stepper.stepperChuteStressTest.telemetry.driverTempAtStart'),
									snapshotTempBand()
								]
							])}
							<div class="mt-2 border-t border-line pt-2">
								{@render facts([
									['GCONF', hex32(telemetrySettings.registers?.['gconf']), 'font-mono text-ink'],
									['CHOPCONF', hex32(telemetrySettings.registers?.['chopconf']), 'font-mono text-ink'],
									['DRV_STATUS', hex32(telemetrySettings.registers?.['drv_status']), 'font-mono text-ink'],
									['PWM_SCALE', hex32(telemetrySettings.registers?.['pwm_scale']), 'font-mono text-ink']
								])}
							</div>
						</div>
					{/if}

					<div class="flex flex-wrap gap-x-5 gap-y-2">
						<Checkbox bind:checked={showSg}>{@render swatch('bg-primary')} SG_RESULT</Checkbox>
						<Checkbox bind:checked={showPwm}>{@render swatch('bg-danger')} PWM_SCALE</Checkbox>
						<Checkbox bind:checked={showCs}>{@render swatch('bg-warning')} CS_ACTUAL</Checkbox>
						<Checkbox bind:checked={showTstep}>{@render swatch('bg-success')} TSTEP</Checkbox>
					</div>

					<ChuteStressTelemetryChart points={telemetryPoints} {showSg} {showCs} {showPwm} {showTstep} height={260} />

					<p class="text-sm text-ink-muted">
						{$_('components.settings.stepper.stepperChuteStressTest.telemetry.scaleNote')}
						{$_('components.settings.stepper.stepperChuteStressTest.telemetry.readingsCount', {
							values: { count: telemetryPoints.length }
						})}
					</p>
					{@render facts([
						...(sgStat
							? [
									[
										'SG_RESULT',
										$_('components.settings.stepper.stepperChuteStressTest.telemetry.rangeValue', {
											values: { min: sgStat.min, max: sgStat.max, last: sgStat.last }
										})
									] as [string, string]
								]
							: []),
						...(pwmStat
							? [
									[
										'PWM_SCALE',
										$_('components.settings.stepper.stepperChuteStressTest.telemetry.rangeValue', {
											values: { min: pwmStat.min, max: pwmStat.max, last: pwmStat.last }
										})
									] as [string, string]
								]
							: []),
						...(csStat
							? [
									[
										'CS_ACTUAL',
										$_('components.settings.stepper.stepperChuteStressTest.telemetry.rangeValue', {
											values: { min: csStat.min, max: csStat.max, last: csStat.last }
										})
									] as [string, string]
								]
							: []),
						...(tstepStat
							? [
									[
										'TSTEP',
										$_(
											'components.settings.stepper.stepperChuteStressTest.telemetry.rangeValueSimple',
											{ values: { min: tstepStat.min, max: tstepStat.max } }
										)
									] as [string, string]
								]
							: []),
						[
							$_('components.settings.stepper.stepperChuteStressTest.telemetry.overheatReadings'),
							String(warnCount),
							warnCount > 0 ? 'text-danger-ink' : undefined
						]
					])}
				{/if}
			</div>
		{/if}
	</div>
</Panel>
