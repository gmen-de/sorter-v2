<script lang="ts">
	import { getBackendHttpBase, machineHttpBaseUrlFromWsUrl } from '$lib/backend';
	import { getMachinesContext } from '$lib/machines/context';
	import Alert from '$lib/components/ui/Alert.svelte';
	import Badge from '$lib/components/ui/Badge.svelte';
	import Button from '$lib/components/ui/Button.svelte';
	import Field from '$lib/components/ui/Field.svelte';
	import Input from '$lib/components/ui/Input.svelte';
	import Panel from '$lib/components/ui/Panel.svelte';
	import Select from '$lib/components/ui/Select.svelte';
	import Stat from '$lib/components/ui/Stat.svelte';

	// Teach the chute its bins by motor steps: move it by hand, zero the step
	// counter, set the 0° (home) correction, then save the position it stands
	// on for each bin. A taught position is stored as that bin's correction
	// (microsteps) on top of the aiming geometry, so the sorter itself uses it
	// from the next move on. Mirrors chute_debug_ui.py, without LEDs and fans.

	const manager = getMachinesContext();
	const POLL_MS = 300;

	type Live = {
		live_available: boolean;
		chute_position_sensor: string | null;
		stepper_microsteps: number | null;
		stepper_stopped: boolean | null;
		homed: boolean | null;
		encoder_count: number | null;
		homing_active?: boolean | null;
	};
	type BinRow = {
		section_index: number;
		bin_index: number;
		geometry_usteps: number;
		correction_usteps: number;
		target_usteps: number;
		taught: boolean;
	};

	let live = $state<Live | null>(null);
	let layers = $state<{ layer_index: number; bin_count: number }[]>([]);
	let layerIndex = $state(0);
	let bins = $state<BinRow[]>([]);
	let usteps_per_rev = $state(7680);

	let speed = $state('500');
	let acceleration = $state('5000');
	let stepsInput = $state('200');
	let targetInput = $state('0');
	let homeCorrection = $state('');
	let savedHomeCorrection = $state<number | null>(null);

	let errorMsg = $state<string | null>(null);
	let statusMsg = $state('');
	let busy = $state(false);
	let homingBusy = $state(false);
	let loadedMachineKey = $state('');
	let liveInFlight = false;

	function backendBase(): string {
		return (
			machineHttpBaseUrlFromWsUrl(
				manager.selectedMachine?.status === 'connected' ? manager.selectedMachine.url : null
			) ?? getBackendHttpBase()
		);
	}

	function num(v: string): number {
		return Number(v.trim());
	}
	function validSpeed(): number | null {
		const v = num(speed);
		return Number.isInteger(v) && v > 0 ? v : null;
	}
	function validAccel(): number | null {
		const v = num(acceleration);
		return Number.isInteger(v) && v > 0 ? v : null;
	}
	function deg(usteps: number | null | undefined): string {
		return usteps === null || usteps === undefined
			? '--'
			: `${((usteps / usteps_per_rev) * 360).toFixed(2)}°`;
	}

	async function call(path: string, body?: unknown, method = 'POST'): Promise<any> {
		const res = await fetch(`${backendBase()}${path}`, {
			method,
			headers: { 'Content-Type': 'application/json' },
			body: body === undefined ? undefined : JSON.stringify(body)
		});
		if (!res.ok) {
			const detail = await res.json().catch(() => null);
			throw new Error(
				typeof detail?.detail === 'string' ? detail.detail : `HTTP ${res.status}`
			);
		}
		return res.json().catch(() => ({}));
	}

	async function act(fn: () => Promise<string | void>) {
		if (busy) return;
		busy = true;
		errorMsg = null;
		try {
			const msg = await fn();
			statusMsg = msg ?? '';
		} catch (e: any) {
			errorMsg = e.message ?? 'Failed';
		} finally {
			busy = false;
			void loadLive();
		}
	}

	async function loadLive() {
		if (liveInFlight) return;
		liveInFlight = true;
		try {
			const res = await fetch(`${backendBase()}/api/hardware-config/chute/live`);
			if (res.ok) live = await res.json();
		} catch {
			// keep the last good reading
		} finally {
			liveInFlight = false;
		}
	}

	async function loadLayout() {
		try {
			const res = await fetch(`${backendBase()}/api/bins/layout`);
			if (!res.ok) throw new Error(await res.text());
			const data = await res.json();
			layers = Array.isArray(data?.layers)
				? data.layers.map((l: any) => ({ layer_index: l.layer_index, bin_count: l.bin_count }))
				: [];
			if (!layers.find((l) => l.layer_index === layerIndex) && layers.length)
				layerIndex = layers[0].layer_index;
		} catch (e: any) {
			errorMsg = e.message ?? 'Failed to load the bin layout';
		}
	}

	async function loadBins() {
		try {
			const res = await fetch(
				`${backendBase()}/api/chute-calibration/bins?layer_index=${layerIndex}`
			);
			if (!res.ok) {
				bins = [];
				return;
			}
			const data = await res.json();
			bins = data.bins ?? [];
			if (Number.isFinite(data.usteps_per_rev)) usteps_per_rev = data.usteps_per_rev;
		} catch {
			bins = [];
		}
	}

	async function loadHomeCorrection() {
		try {
			const res = await fetch(`${backendBase()}/api/hardware-config/chute`);
			if (!res.ok) return;
			const c = await res.json();
			if (Number.isFinite(c?.home_correction_usteps)) {
				savedHomeCorrection = c.home_correction_usteps;
				homeCorrection = String(c.home_correction_usteps);
			}
		} catch {
			// shown empty
		}
	}

	const jog = (dir: 1 | -1) =>
		act(async () => {
			const s = validSpeed();
			const a = validAccel();
			if (s === null || a === null) throw new Error('Speed and acceleration must be whole numbers above 0.');
			await call('/api/chute-calibration/jog', { speed: dir * s, acceleration: a });
		});
	const stop = () => act(async () => void (await call('/api/chute-calibration/stop')));

	const moveSteps = (sign: 1 | -1) =>
		act(async () => {
			const s = validSpeed();
			const a = validAccel();
			const n = num(stepsInput);
			if (s === null || a === null || !Number.isInteger(n) || n <= 0)
				throw new Error('Steps, speed and acceleration must be whole numbers above 0.');
			await call('/api/chute-calibration/move-steps', {
				steps: sign * n,
				speed: s,
				acceleration: a
			});
		});

	const moveTo = (usteps: number) =>
		act(async () => {
			const s = validSpeed();
			const a = validAccel();
			if (s === null || a === null || !Number.isInteger(usteps))
				throw new Error('Target, speed and acceleration must be whole numbers.');
			await call('/api/chute-calibration/move-to', { usteps, speed: s, acceleration: a });
			return `Moving to step ${usteps}.`;
		});

	const zeroCounter = () =>
		act(async () => {
			await call('/api/chute-calibration/zero-counter');
			return 'Step counter set to 0 at the current position.';
		});

	const saveHomeCorrection = () =>
		act(async () => {
			const v = num(homeCorrection);
			if (!Number.isInteger(v)) throw new Error('The 0° correction must be a whole number of microsteps.');
			await call('/api/hardware-config/chute', { home_correction_usteps: v });
			savedHomeCorrection = v;
			return 'The 0° correction is saved; it applies at the next homing.';
		});

	async function homeChute() {
		if (homingBusy) return;
		homingBusy = true;
		errorMsg = null;
		try {
			const data = await call('/api/hardware-config/chute/calibrate/find-endstop');
			statusMsg = data?.message ?? 'Chute homed.';
		} catch (e: any) {
			errorMsg = e.message ?? 'Failed to home the chute.';
		} finally {
			homingBusy = false;
			void loadLive();
			void loadBins();
		}
	}

	const goToBin = (b: BinRow) => moveTo(b.target_usteps);

	const learnBin = (b: BinRow) =>
		act(async () => {
			const r = await call('/api/chute-calibration/learn-bin', {
				layer_index: layerIndex,
				section_index: b.section_index,
				bin_index: b.bin_index
			});
			await loadBins();
			return `Section ${b.section_index + 1}, bin ${b.bin_index + 1} taught at step ${r.target_usteps} (${r.correction_usteps >= 0 ? '+' : ''}${r.correction_usteps} from the geometry).`;
		});

	const clearBin = (b: BinRow) =>
		act(async () => {
			await call('/api/chute-calibration/clear-bin', {
				layer_index: layerIndex,
				section_index: b.section_index,
				bin_index: b.bin_index
			});
			await loadBins();
			return `Section ${b.section_index + 1}, bin ${b.bin_index + 1} back to the geometry position.`;
		});

	$effect(() => {
		const key = manager.selectedMachine?.url ?? '';
		if (key === loadedMachineKey) return;
		loadedMachineKey = key;
		void loadLayout();
		void loadLive();
		void loadHomeCorrection();
	});

	$effect(() => {
		// Reload the table when the layer changes.
		layerIndex;
		void loadBins();
	});

	$effect(() => {
		const h = setInterval(() => void loadLive(), POLL_MS);
		return () => clearInterval(h);
	});

	const sections = $derived.by(() => {
		const out: { section: number; rows: BinRow[] }[] = [];
		for (const b of bins) {
			let g = out.find((s) => s.section === b.section_index);
			if (!g) out.push((g = { section: b.section_index, rows: [] }));
			g.rows.push(b);
		}
		return out;
	});
</script>

{#if errorMsg}
	<Alert tone="danger">{errorMsg}</Alert>
{/if}

<Panel
	title="Position"
	description="The chute's own step counter (microsteps; one revolution is {usteps_per_rev}) and the encoder, polled live."
>
	{#snippet actions()}
		<Button
			variant="primary"
			size="sm"
			loading={homingBusy}
			disabled={homingBusy || !!live?.homing_active}
			onclick={() => void homeChute()}
		>
			Home chute
		</Button>
	{/snippet}
	{#if !live?.live_available}
		<div class="px-(--pad-panel) pb-(--pad-panel) text-sm text-ink-muted">
			Hardware not initialized — start the machine to control the chute.
		</div>
	{:else}
		<div class="grid grid-cols-2 gap-3 px-(--pad-panel) pb-(--pad-panel) sm:grid-cols-4">
			<Stat label="Step counter" value={live.stepper_microsteps ?? '--'} />
			<Stat label="Angle" value={deg(live.stepper_microsteps)} />
			<Stat label="Encoder count" value={live.encoder_count ?? '--'} />
			<Stat
				label="State"
				value={live.stepper_stopped ? 'Stopped' : 'Moving'}
				tone={live.homed ? undefined : 'warning'}
			/>
		</div>
		{#if !live.homed}
			<div class="px-(--pad-panel) pb-(--pad-panel)">
				<Badge tone="warning">Not homed — moves to a step position are refused until it is.</Badge>
			</div>
		{/if}
	{/if}
</Panel>

<Panel
	title="Manual control"
	description="Run the chute at a speed, or move it by a number of steps. Speed and acceleration apply to every move on this tab."
>
	<div class="flex flex-col gap-4 px-(--pad-panel) pb-(--pad-panel)">
		<div class="flex flex-wrap items-end gap-3">
			<Field label="Speed (µsteps/s)" for="cal-speed">
				<Input
					id="cal-speed"
					type="number"
					step={50}
					value={speed}
					oninput={(e) => (speed = (e.currentTarget as HTMLInputElement).value)}
					class="w-28"
				/>
			</Field>
			<Field label="Acceleration (µsteps/s²)" for="cal-accel">
				<Input
					id="cal-accel"
					type="number"
					step={500}
					value={acceleration}
					oninput={(e) => (acceleration = (e.currentTarget as HTMLInputElement).value)}
					class="w-32"
				/>
			</Field>
			<Button disabled={!live?.live_available} onclick={() => void jog(-1)}>◄ Backward</Button>
			<Button variant="danger" onclick={() => void stop()}>Stop</Button>
			<Button disabled={!live?.live_available} onclick={() => void jog(1)}>Forward ►</Button>
		</div>

		<div class="flex flex-wrap items-end gap-3">
			<Field label="Steps" for="cal-steps">
				<Input
					id="cal-steps"
					type="number"
					step={10}
					value={stepsInput}
					oninput={(e) => (stepsInput = (e.currentTarget as HTMLInputElement).value)}
					class="w-28"
				/>
			</Field>
			<Button disabled={!live?.live_available} onclick={() => void moveSteps(-1)}>− Steps</Button>
			<Button disabled={!live?.live_available} onclick={() => void moveSteps(1)}>+ Steps</Button>
			<Field label="Go to step position" for="cal-target">
				<Input
					id="cal-target"
					type="number"
					step={1}
					value={targetInput}
					oninput={(e) => (targetInput = (e.currentTarget as HTMLInputElement).value)}
					class="w-28"
				/>
			</Field>
			<Button disabled={!live?.live_available} onclick={() => void moveTo(num(targetInput))}>
				Go
			</Button>
		</div>
		{#if statusMsg}<p class="text-sm text-ink-muted">{statusMsg}</p>{/if}
	</div>
</Panel>

<Panel
	title="Zero"
	description="Zero the step counter where the chute stands, and set the 0° correction (microsteps added to the firmware's home position)."
>
	<div class="flex flex-wrap items-end gap-3 px-(--pad-panel) pb-(--pad-panel)">
		<Button
			disabled={!live?.live_available || !live?.stepper_stopped}
			onclick={() => void zeroCounter()}
		>
			Zero step counter
		</Button>
		<Field label="0° correction (microsteps)" for="cal-home-correction">
			<Input
				id="cal-home-correction"
				type="number"
				step={1}
				value={homeCorrection}
				oninput={(e) => (homeCorrection = (e.currentTarget as HTMLInputElement).value)}
				class="w-32"
			/>
		</Field>
		<Button
			variant="primary"
			disabled={homeCorrection.trim() === String(savedHomeCorrection ?? '')}
			onclick={() => void saveHomeCorrection()}
		>
			Save
		</Button>
	</div>
	<p class="px-(--pad-panel) pb-(--pad-panel) text-sm text-ink-muted">
		Zeroing moves the origin the bin positions below are measured from, so do it before teaching
		them. The 0° correction applies at the next homing.
	</p>
</Panel>

<Panel
	title="Bin positions"
	description="Move the chute onto a bin by hand, then save it: the step position it stands on is taught to that bin. Go to drives to the bin's current position to check it."
>
	<div class="flex flex-col gap-3 px-(--pad-panel) pb-(--pad-panel)">
		<Field label="Layer" for="cal-layer">
			<Select
				id="cal-layer"
				value={String(layerIndex)}
				onchange={(v) => (layerIndex = Number(v))}
				options={layers.map((l) => ({
					value: String(l.layer_index),
					label: `Layer ${l.layer_index + 1}`
				}))}
			/>
		</Field>
		{#if bins.length === 0}
			<p class="text-sm text-ink-muted">No bins to show — start the machine first.</p>
		{/if}
		{#each sections as s (s.section)}
			<div class="flex flex-col gap-1">
				<span class="text-sm font-medium text-ink">Section {s.section + 1}</span>
				<div class="overflow-x-auto">
					<table class="w-full text-sm">
						<thead>
							<tr class="text-left text-ink-muted">
								<th class="py-1 pr-3 font-normal">Bin</th>
								<th class="py-1 pr-3 font-normal">Geometry</th>
								<th class="py-1 pr-3 font-normal">Taught</th>
								<th class="py-1 pr-3 font-normal">Drives to</th>
								<th class="py-1"></th>
							</tr>
						</thead>
						<tbody>
							{#each s.rows as b (b.bin_index)}
								<tr class="border-t border-line">
									<td class="py-1 pr-3">{b.bin_index + 1}</td>
									<td class="num py-1 pr-3">{b.geometry_usteps}</td>
									<td class="num py-1 pr-3">
										{#if b.taught}
											{b.correction_usteps >= 0 ? '+' : ''}{b.correction_usteps}
										{:else}
											<span class="text-ink-muted">—</span>
										{/if}
									</td>
									<td class="num py-1 pr-3">{b.target_usteps} ({deg(b.target_usteps)})</td>
									<td class="flex gap-2 py-1">
										<Button
											size="sm"
											disabled={!live?.live_available}
											onclick={() => void goToBin(b)}
										>
											Go to
										</Button>
										<Button
											size="sm"
											variant="primary"
											disabled={!live?.live_available || !live?.stepper_stopped}
											onclick={() => void learnBin(b)}
										>
											Save here
										</Button>
										{#if b.taught}
											<Button size="sm" onclick={() => void clearBin(b)}>Reset</Button>
										{/if}
									</td>
								</tr>
							{/each}
						</tbody>
					</table>
				</div>
			</div>
		{/each}
	</div>
</Panel>
