<script lang="ts">
	import { _ } from 'svelte-i18n';
	import { onDestroy, onMount } from 'svelte';
	import { getBackendHttpBase, machineHttpBaseUrlFromWsUrl } from '$lib/backend';
	import Panel from '$lib/components/ui/Panel.svelte';
	import EmptyState from '$lib/components/ui/EmptyState.svelte';
	import Stat from '$lib/components/ui/Stat.svelte';
	import ProgressBar from '$lib/components/ui/ProgressBar.svelte';
	import Badge from '$lib/components/ui/Badge.svelte';
	import Field from '$lib/components/ui/Field.svelte';
	import PageHeader from '$lib/components/ui/PageHeader.svelte';
	import Alert from '$lib/components/ui/Alert.svelte';
	import Button from '$lib/components/ui/Button.svelte';
	import Input from '$lib/components/ui/Input.svelte';
	import { getMachinesContext } from '$lib/machines/context';

	const manager = getMachinesContext();

	type StressEvent = {
		id: number;
		created_at: number;
		event_type: string;
		phase: string | null;
		details: Record<string, unknown>;
	};

	type StressRun = {
		id: string;
		started_at: number;
		ended_at: number | null;
		duration_target_s: number;
		stepper_speed_microsteps_per_sec: number;
		chute_speed_microsteps_per_sec: number;
		chute_max_deg: number;
		status: string;
		total_time_s: number;
		current_phase: string | null;
		current_segment?: number;
		error: string | null;
		hardware?: {
			steppers: string[];
			servo_count: number;
			led_output_count: number;
			perception_workers_alive: number;
			camera_roles: string[];
		};
		events?: StressEvent[];
	};

	let durationMinutes = $state(10);
	let stepperSpeed = $state(6000);
	let chuteSpeed = $state(3000);
	let chuteMax = $state(345);
	let active = $state(false);
	let run = $state<StressRun | null>(null);
	let runs = $state<StressRun[]>([]);
	let busy = $state(false);
	let errorMsg = $state<string | null>(null);
	let pollTimer: ReturnType<typeof setInterval> | null = null;

	const progressPercent = $derived(
		run ? Math.min(100, (run.total_time_s / run.duration_target_s) * 100) : 0
	);

	function backendBase(): string {
		return (
			machineHttpBaseUrlFromWsUrl(
				manager.selectedMachine?.status === 'connected' ? manager.selectedMachine.url : null
			) ?? getBackendHttpBase()
		);
	}

	async function readError(response: Response): Promise<string> {
		try {
			const payload = await response.json();
			if (typeof payload?.detail === 'string') return payload.detail;
		} catch {
			return $_('routes.settings.powerStress.requestFailed', { values: { status: response.status } });
		}
		return $_('routes.settings.powerStress.requestFailed', { values: { status: response.status } });
	}

	async function loadStatus() {
		try {
			const response = await fetch(`${backendBase()}/api/power-stress/status`);
			if (!response.ok) throw new Error(await readError(response));
			const payload = await response.json();
			active = Boolean(payload.active);
			if (payload.run) run = payload.run;
		} catch (error) {
			errorMsg = error instanceof Error ? error.message : String(error);
		}
	}

	async function loadRuns() {
		try {
			const response = await fetch(`${backendBase()}/api/power-stress/runs?limit=20`);
			if (!response.ok) throw new Error(await readError(response));
			runs = (await response.json()).runs ?? [];
		} catch (error) {
			errorMsg = error instanceof Error ? error.message : String(error);
		}
	}

	async function startTest() {
		busy = true;
		errorMsg = null;
		try {
			const response = await fetch(`${backendBase()}/api/power-stress/start`, {
				method: 'POST',
				headers: { 'Content-Type': 'application/json' },
				body: JSON.stringify({
					duration_s: durationMinutes * 60,
					stepper_speed_microsteps_per_sec: stepperSpeed,
					chute_speed_microsteps_per_sec: chuteSpeed,
					chute_max_deg: chuteMax
				})
			});
			if (!response.ok) throw new Error(await readError(response));
			const payload = await response.json();
			active = Boolean(payload.active);
			run = payload.run;
			await loadRuns();
		} catch (error) {
			errorMsg = error instanceof Error ? error.message : String(error);
		} finally {
			busy = false;
		}
	}

	async function stopTest() {
		busy = true;
		errorMsg = null;
		try {
			const response = await fetch(`${backendBase()}/api/power-stress/stop`, {
				method: 'POST'
			});
			if (!response.ok) throw new Error(await readError(response));
			const payload = await response.json();
			active = Boolean(payload.active);
			run = payload.run;
		} catch (error) {
			errorMsg = error instanceof Error ? error.message : String(error);
		} finally {
			busy = false;
		}
	}

	async function downloadRun(runId: string) {
		errorMsg = null;
		try {
			const response = await fetch(`${backendBase()}/api/power-stress/runs/${runId}`);
			if (!response.ok) throw new Error(await readError(response));
			const payload = await response.json();
			const blob = new Blob([JSON.stringify(payload, null, 2)], { type: 'application/json' });
			const url = URL.createObjectURL(blob);
			const link = document.createElement('a');
			link.href = url;
			link.download = `power-stress-${runId}.json`;
			link.click();
			URL.revokeObjectURL(url);
		} catch (error) {
			errorMsg = error instanceof Error ? error.message : String(error);
		}
	}

	function formatTime(timestamp: number | null): string {
		return timestamp ? new Date(timestamp * 1000).toLocaleString() : '—';
	}

	function formatDuration(seconds: number): string {
		const minutes = Math.floor(seconds / 60);
		const remainder = Math.round(seconds % 60);
		return `${minutes}m ${remainder}s`;
	}

	onMount(() => {
		void loadStatus();
		void loadRuns();
		pollTimer = setInterval(() => {
			void loadStatus();
			if (!active) void loadRuns();
		}, 1000);
	});

	onDestroy(() => {
		if (pollTimer) clearInterval(pollTimer);
	});
</script>

<svelte:head><title>{$_('routes.settings.powerStress.pageTitle')}</title></svelte:head>

<PageHeader
	title={$_('routes.settings.powerStress.title')}
	description={$_('routes.settings.powerStress.description')}
/>

<Alert tone="info">
	{$_('routes.settings.powerStress.alertInfo', { values: { chuteMax } })}
</Alert>

<Panel title={$_('routes.settings.powerStress.sequence.panelTitle')} flush>
	<ol class="grid grid-cols-1 gap-px bg-line sm:grid-cols-3">
		<li class="bg-surface px-(--pad-panel) py-(--pad-row)">
			<div class="text-sm font-medium text-ink">{$_('routes.settings.powerStress.sequence.steady.title')}</div>
			<p class="mt-0.5 text-sm text-ink-muted">
				{$_('routes.settings.powerStress.sequence.steady.description')}
			</p>
		</li>
		<li class="bg-surface px-(--pad-panel) py-(--pad-row)">
			<div class="text-sm font-medium text-ink">{$_('routes.settings.powerStress.sequence.random.title')}</div>
			<p class="mt-0.5 text-sm text-ink-muted">
				{$_('routes.settings.powerStress.sequence.random.description')}
			</p>
		</li>
		<li class="bg-surface px-(--pad-panel) py-(--pad-row)">
			<div class="text-sm font-medium text-ink">{$_('routes.settings.powerStress.sequence.mixed.title')}</div>
			<p class="mt-0.5 text-sm text-ink-muted">
				{$_('routes.settings.powerStress.sequence.mixed.description')}
			</p>
		</li>
	</ol>
</Panel>

<Panel title={$_('routes.settings.powerStress.settings.panelTitle')}>
	<div class="grid grid-cols-1 gap-4 sm:grid-cols-2">
		<Field label={$_('routes.settings.powerStress.settings.movingTime')} for="stress-minutes">
			<Input id="stress-minutes" type="number" bind:value={durationMinutes} disabled={active} unit="min" />
		</Field>
		<Field label={$_('routes.settings.powerStress.settings.stepperTopSpeed')} for="stress-stepper-speed">
			<Input id="stress-stepper-speed" type="number" bind:value={stepperSpeed} disabled={active} unit="µsteps/s" />
		</Field>
		<Field label={$_('routes.settings.powerStress.settings.chuteSpeed')} for="stress-chute-speed">
			<Input id="stress-chute-speed" type="number" bind:value={chuteSpeed} disabled={active} unit="µsteps/s" />
		</Field>
		<Field label={$_('routes.settings.powerStress.settings.chuteLimit')} for="stress-chute-max">
			<Input id="stress-chute-max" type="number" bind:value={chuteMax} disabled={active} unit="°" />
		</Field>
	</div>
	{#snippet footer()}
		{#if active}
			<Button variant="danger" loading={busy} onclick={stopTest}>{$_('routes.settings.powerStress.settings.stopSafely')}</Button>
		{:else}
			<Button variant="primary" loading={busy} onclick={startTest}>{$_('routes.settings.powerStress.settings.start')}</Button>
		{/if}
	{/snippet}
</Panel>

{#if errorMsg}
	<Alert tone="danger">{errorMsg}</Alert>
{/if}

{#if run}
	<Panel title={$_('routes.settings.powerStress.thisRun.panelTitle')} flush>
		{#snippet actions()}
			<Button size="sm" onclick={() => run && downloadRun(run.id)}>{$_('routes.settings.powerStress.thisRun.downloadJson')}</Button>
		{/snippet}
		<div class="flex flex-col gap-3 px-(--pad-panel) pb-4">
			<div class="flex flex-wrap items-center gap-2">
				<Badge tone={run.status === 'running' ? 'primary' : 'neutral'} dot>{run.status}</Badge>
				<span class="num text-sm text-ink-muted">
					{run.current_phase ?? $_('routes.settings.powerStress.thisRun.finished')}{run.current_segment
						? $_('routes.settings.powerStress.thisRun.part', { values: { n: run.current_segment } })
						: ''},
					{formatDuration(run.total_time_s)} {$_('routes.settings.powerStress.thisRun.of')} {formatDuration(run.duration_target_s)}
				</span>
			</div>
			<ProgressBar label={$_('routes.settings.powerStress.thisRun.progress')} value={progressPercent} />
			{#if run.error}
				<Alert tone="danger">{run.error}</Alert>
			{/if}
		</div>
		{#if run.hardware}
			<div class="grid grid-cols-2 gap-px border-t border-line bg-line sm:grid-cols-4">
				<div class="bg-surface"><Stat label={$_('routes.settings.powerStress.thisRun.steppers')} value={run.hardware.steppers.length} /></div>
				<div class="bg-surface"><Stat label={$_('routes.settings.powerStress.thisRun.servos')} value={run.hardware.servo_count} /></div>
				<div class="bg-surface"><Stat label={$_('routes.settings.powerStress.thisRun.lightOutputs')} value={run.hardware.led_output_count} /></div>
				<div class="bg-surface"><Stat label={$_('routes.settings.powerStress.thisRun.visionWorkers')} value={run.hardware.perception_workers_alive} /></div>
			</div>
		{/if}
		{#if run.events?.length}
			<div class="max-h-72 overflow-auto border-t border-line">
				<table class="data-table">
					<thead><tr><th>{$_('routes.settings.powerStress.thisRun.when')}</th><th>{$_('routes.settings.powerStress.thisRun.what')}</th><th>{$_('routes.settings.powerStress.thisRun.phase')}</th></tr></thead>
					<tbody>
						{#each [...run.events].reverse() as event (event.id)}
							<tr>
								<td class="num text-ink-muted">{formatTime(event.created_at)}</td>
								<td>{event.event_type.replaceAll('_', ' ')}</td>
								<td class="text-ink-muted">{event.phase ?? ''}</td>
							</tr>
						{/each}
					</tbody>
				</table>
			</div>
		{/if}
	</Panel>
{/if}

<Panel title={$_('routes.settings.powerStress.pastRuns.panelTitle')} flush>
	{#if runs.length === 0}
		<p class="px-(--pad-panel) pb-(--pad-panel) text-sm text-ink-muted">{$_('routes.settings.powerStress.pastRuns.none')}</p>
	{:else}
		<div class="overflow-x-auto">
			<table class="data-table">
				<thead>
					<tr><th>{$_('routes.settings.powerStress.pastRuns.started')}</th><th>{$_('routes.settings.powerStress.pastRuns.status')}</th><th class="num">{$_('routes.settings.powerStress.pastRuns.duration')}</th><th><span class="sr-only">{$_('routes.settings.powerStress.pastRuns.downloadSr')}</span></th></tr>
				</thead>
				<tbody>
					{#each runs as item (item.id)}
						<tr>
							<td>{formatTime(item.started_at)}</td>
							<td class="capitalize">{item.status}</td>
							<td class="num">{formatDuration(item.total_time_s)}</td>
							<td class="text-right">
								<Button variant="ghost" size="sm" onclick={() => downloadRun(item.id)}>{$_('routes.settings.powerStress.pastRuns.json')}</Button>
							</td>
						</tr>
					{/each}
				</tbody>
			</table>
		</div>
	{/if}
</Panel>
