<script lang="ts">
	import { _ } from 'svelte-i18n';
	import { getBackendHttpBase, machineHttpBaseUrlFromWsUrl } from '$lib/backend';
	import { getMachinesContext } from '$lib/machines/context';
	import Panel from '$lib/components/ui/Panel.svelte';
	import Select from '$lib/components/ui/Select.svelte';
	import Field from '$lib/components/ui/Field.svelte';
	import PageHeader from '$lib/components/ui/PageHeader.svelte';
	import Alert from '$lib/components/ui/Alert.svelte';
	import Button from '$lib/components/ui/Button.svelte';
	import Input from '$lib/components/ui/Input.svelte';
	import { STEPPER_GEAR_RATIOS } from '$lib/settings/stepper-control';
	import { stepperLabels, type StepperKey } from '$lib/settings/stations';

	const manager = getMachinesContext();

	const STEPPERS: StepperKey[] = ['c_channel_1', 'c_channel_2', 'c_channel_3', 'c_channel_4', 'carousel', 'chute'];

	type JitterSettings = {
		stepper: StepperKey;
		amplitudeDeg: number;
		cycles: number;
		speed: number;
		acceleration: number;
	};

	const DEFAULTS: JitterSettings = {
		stepper: 'c_channel_1',
		amplitudeDeg: 6,
		cycles: 12,
		speed: 5000,
		acceleration: 100000
	};

	type JitterPreset = {
		id: string;
		amplitudeDeg: number;
		cycles: number;
		speed: number;
		acceleration: number;
	};

	// Amplitude is per-stroke MOTOR degrees (before gear reduction); the big gear
	// ratio means even "medium" motor amplitudes are a small rotor swing. Speeds
	// in µsteps/s (firmware caps at 60000); accel in µsteps/s². "Heavy Stuck"
	// (6° / 12c / 5000 / 100000) is the reference that visibly worked — medium
	// travel, medium strength, medium length. The others are variants around it:
	// travel = amplitude, length = cycles, strength = speed + accel.
	const PRESETS: JitterPreset[] = [
		{ id: 'heavyStuck', amplitudeDeg: 6, cycles: 12, speed: 5000, acceleration: 100000 },
		{ id: 'quickStuck', amplitudeDeg: 6, cycles: 6, speed: 5000, acceleration: 100000 },
		{ id: 'briefStuck', amplitudeDeg: 6, cycles: 3, speed: 5000, acceleration: 100000 },
		{ id: 'sharpShort', amplitudeDeg: 6, cycles: 6, speed: 6500, acceleration: 180000 },
		{ id: 'hardSnap', amplitudeDeg: 6, cycles: 3, speed: 7000, acceleration: 220000 },
		{ id: 'softLong', amplitudeDeg: 5, cycles: 30, speed: 3500, acceleration: 55000 },
		{ id: 'gentleMarathon', amplitudeDeg: 4, cycles: 50, speed: 3000, acceleration: 45000 },
		{ id: 'bigTravel', amplitudeDeg: 9, cycles: 12, speed: 5000, acceleration: 110000 },
		{ id: 'bigBrief', amplitudeDeg: 9, cycles: 5, speed: 6000, acceleration: 150000 },
		{ id: 'bigHard', amplitudeDeg: 9, cycles: 8, speed: 6500, acceleration: 200000 },
		{ id: 'maxShake', amplitudeDeg: 13, cycles: 12, speed: 6500, acceleration: 190000 },
		{ id: 'wideSoft', amplitudeDeg: 9, cycles: 16, speed: 3500, acceleration: 55000 },
		{ id: 'fastBuzz', amplitudeDeg: 6, cycles: 24, speed: 6000, acceleration: 150000 },
		{ id: 'strongLong', amplitudeDeg: 8, cycles: 30, speed: 6000, acceleration: 140000 }
	];

	const STORAGE_KEY = 'jitter-test:settings';

	function loadSettings(): JitterSettings {
		try {
			if (typeof localStorage === 'undefined') return { ...DEFAULTS };
			const raw = localStorage.getItem(STORAGE_KEY);
			if (!raw) return { ...DEFAULTS };
			return { ...DEFAULTS, ...JSON.parse(raw) };
		} catch {
			return { ...DEFAULTS };
		}
	}

	let settings = $state<JitterSettings>(loadSettings());
	let busy = $state(false);
	let statusMsg = $state<string | null>(null);
	let errorMsg = $state<string | null>(null);

	$effect(() => {
		try {
			localStorage.setItem(STORAGE_KEY, JSON.stringify(settings));
		} catch {
			/* ignore persistence failures */
		}
	});

	function currentBackendBaseUrl(): string {
		return (
			machineHttpBaseUrlFromWsUrl(
				manager.selectedMachine?.status === 'connected' ? manager.selectedMachine.url : null
			) ?? getBackendHttpBase()
		);
	}

	const gearRatio = $derived(STEPPER_GEAR_RATIOS[settings.stepper] ?? 1);
	const outputAmplitudeDeg = $derived(settings.amplitudeDeg / gearRatio);

	async function readError(res: Response): Promise<string> {
		try {
			const data = await res.json();
			if (typeof data?.detail === 'string') return data.detail;
			if (typeof data?.message === 'string') return data.message;
		} catch {
			/* fall through */
		}
		return $_('routes.settings.jitterTest.status.requestFailed', { values: { status: res.status } });
	}

	async function runJitter() {
		busy = true;
		errorMsg = null;
		statusMsg = null;
		try {
			const params = new URLSearchParams({
				stepper: settings.stepper,
				amplitude_deg: String(settings.amplitudeDeg),
				cycles: String(settings.cycles),
				speed: String(settings.speed),
				acceleration: String(settings.acceleration)
			});
			const res = await fetch(`${currentBackendBaseUrl()}/stepper/jitter?${params.toString()}`, {
				method: 'POST'
			});
			if (!res.ok) {
				throw new Error(await readError(res));
			}
			const payload = await res.json();
			statusMsg = $_('routes.settings.jitterTest.status.jittering', {
				values: {
					stepper: stepperLabels[settings.stepper],
					cycles: payload.cycles,
					amplitude: settings.amplitudeDeg,
					duration: payload.estimated_duration_s,
					microsteps: payload.amplitude_microsteps
				}
			});
		} catch (e) {
			errorMsg = e instanceof Error ? e.message : String(e);
		} finally {
			busy = false;
		}
	}

	async function stopStepper() {
		errorMsg = null;
		try {
			const res = await fetch(
				`${currentBackendBaseUrl()}/stepper/stop?stepper=${encodeURIComponent(settings.stepper)}`,
				{ method: 'POST' }
			);
			if (!res.ok) throw new Error(await readError(res));
			statusMsg = $_('routes.settings.jitterTest.status.stopped', { values: { stepper: stepperLabels[settings.stepper] } });
		} catch (e) {
			errorMsg = e instanceof Error ? e.message : String(e);
		}
	}

	function resetDefaults() {
		settings = { ...DEFAULTS, stepper: settings.stepper };
	}

	function applyPreset(p: JitterPreset) {
		settings = {
			stepper: settings.stepper,
			amplitudeDeg: p.amplitudeDeg,
			cycles: p.cycles,
			speed: p.speed,
			acceleration: p.acceleration
		};
	}
</script>

<svelte:head><title>{$_('routes.settings.jitterTest.pageTitle')}</title></svelte:head>

<PageHeader
	title={$_('routes.settings.jitterTest.title')}
	description={$_('routes.settings.jitterTest.description')}
/>

<Panel title={$_('routes.settings.jitterTest.motor.panelTitle')}>
	<div class="max-w-64">
		<Select
			label={$_('routes.settings.jitterTest.motor.label')}
			bind:value={settings.stepper}
			options={STEPPERS.map((key) => ({ value: key, label: stepperLabels[key] }))}
		/>
	</div>
</Panel>

<Panel
	title={$_('routes.settings.jitterTest.presets.panelTitle')}
	description={$_('routes.settings.jitterTest.presets.description', { values: { gearRatio: gearRatio.toFixed(1) } })}
	flush
>
	<div
		class="grid grid-cols-1 gap-px border-t border-line bg-line sm:grid-cols-3 sm:[&>:last-child:nth-child(3n+1)]:col-span-3 sm:[&>:last-child:nth-child(3n+2)]:col-span-2"
	>
		{#each PRESETS as p (p.id)}
			{@const selected =
				settings.amplitudeDeg === p.amplitudeDeg &&
				settings.cycles === p.cycles &&
				settings.speed === p.speed &&
				settings.acceleration === p.acceleration}
			<!-- The cell is opaque, so the chosen tint sits on the surface and not on the lines between cells. -->
			<button type="button" aria-pressed={selected} onclick={() => applyPreset(p)} class="bg-surface text-left">
				<span
					class="flex h-full flex-col gap-0.5 px-(--pad-panel) py-(--pad-row) transition-colors {selected
						? 'bg-primary-soft'
						: 'hover:bg-well'}"
				>
					<span class="text-sm font-medium {selected ? 'text-primary-ink' : 'text-ink'}">
						{$_(`routes.settings.jitterTest.presets.items.${p.id}.name`)}
					</span>
					<span class="text-sm text-ink-muted">{$_(`routes.settings.jitterTest.presets.items.${p.id}.blurb`)}</span>
					<span class="num text-sm text-ink-muted">
						±{p.amplitudeDeg}°, {p.cycles} cycles, {p.speed} µsteps/s, {(p.acceleration / 1000).toFixed(0)}k
					</span>
				</span>
			</button>
		{/each}
	</div>
</Panel>

<Panel title={$_('routes.settings.jitterTest.settingsPanelTitle')}>
	<div class="grid grid-cols-1 gap-4 sm:grid-cols-2">
		<Field
			label={$_('routes.settings.jitterTest.fields.amplitude.label')}
			for="jitter-amplitude"
			help={$_('routes.settings.jitterTest.fields.amplitude.help', { values: { outputAmplitudeDeg: outputAmplitudeDeg.toFixed(2), gearRatio: gearRatio.toFixed(2) } })}
		>
			<Input
				id="jitter-amplitude"
				type="number"
				step={0.1}
				bind:value={settings.amplitudeDeg}
				unit={$_('routes.settings.jitterTest.fields.amplitude.unit')}
			/>
		</Field>
		<Field
			label={$_('routes.settings.jitterTest.fields.cycles.label')}
			for="jitter-cycles"
			help={$_('routes.settings.jitterTest.fields.cycles.help')}
		>
			<Input id="jitter-cycles" type="number" step={1} bind:value={settings.cycles} />
		</Field>
		<Field label={$_('routes.settings.jitterTest.fields.speed.label')} for="jitter-speed">
			<Input id="jitter-speed" type="number" step={100} bind:value={settings.speed} unit="µsteps/s" />
		</Field>
		<Field
			label={$_('routes.settings.jitterTest.fields.acceleration.label')}
			for="jitter-accel"
			help={$_('routes.settings.jitterTest.fields.acceleration.help')}
		>
			<Input id="jitter-accel" type="number" step={5000} bind:value={settings.acceleration} unit="µsteps/s²" />
		</Field>
	</div>
	{#snippet footer()}
		<Button variant="ghost" class="mr-auto" onclick={resetDefaults}>{$_('routes.settings.jitterTest.actions.resetDefaults')}</Button>
		<Button variant="danger" onclick={stopStepper}>{$_('routes.settings.jitterTest.actions.stop')}</Button>
		<Button variant="primary" loading={busy} onclick={runJitter}>{$_('routes.settings.jitterTest.actions.jitter')}</Button>
	{/snippet}
</Panel>

{#if statusMsg}
	<Alert tone="info">{statusMsg}</Alert>
{/if}
{#if errorMsg}
	<Alert tone="danger">{errorMsg}</Alert>
{/if}
