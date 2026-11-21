<script lang="ts">
	import { onMount } from 'svelte';
	import Alert from '$lib/components/ui/Alert.svelte';
	import Badge from '$lib/components/ui/Badge.svelte';
	import Button from '$lib/components/ui/Button.svelte';
	import Field from '$lib/components/ui/Field.svelte';
	import Input from '$lib/components/ui/Input.svelte';
	import Panel from '$lib/components/ui/Panel.svelte';
	import Select from '$lib/components/ui/Select.svelte';
	import Spinner from '$lib/components/ui/Spinner.svelte';
	import Stat from '$lib/components/ui/Stat.svelte';
	import { getBackendHttpBase, machineHttpBaseUrlFromWsUrl } from '$lib/backend';
	import { getMachineContext } from '$lib/machines/context';

	type BinEntry = { section_index: number; bin_index: number };
	type LayerLayout = { layer_index: number; bin_count: number; bins: BinEntry[] };

	const machine = getMachineContext();

	function backendBase(): string {
		return machineHttpBaseUrlFromWsUrl(machine.machine?.url) ?? getBackendHttpBase();
	}

	function sleep(ms: number): Promise<void> {
		return new Promise((resolve) => setTimeout(resolve, ms));
	}

	let layersLoading = $state(true);
	let layoutError = $state<string | null>(null);
	let layerLayouts = $state<LayerLayout[]>([]);

	async function loadLayout() {
		layersLoading = true;
		layoutError = null;
		try {
			const res = await fetch(`${backendBase()}/api/bins/layout`);
			if (!res.ok) throw new Error(await res.text());
			const data = await res.json();
			layerLayouts = Array.isArray(data?.layers)
				? data.layers.map((l: any) => ({
						layer_index: l.layer_index,
						bin_count: l.bin_count,
						bins: Array.isArray(l.bins)
							? l.bins.map((b: any) => ({ section_index: b.section_index, bin_index: b.bin_index }))
							: []
					}))
				: [];
		} catch (e: any) {
			layoutError = e?.message ?? 'Failed to load the bin layout.';
		} finally {
			layersLoading = false;
		}
	}

	function sectionsFor(layer: number): number[] {
		const l = layerLayouts.find((x) => x.layer_index === layer);
		if (!l) return [];
		return [...new Set(l.bins.map((b) => b.section_index))].sort((a, b) => a - b);
	}

	function binsFor(layer: number, section: number): number[] {
		const l = layerLayouts.find((x) => x.layer_index === layer);
		if (!l) return [];
		return l.bins
			.filter((b) => b.section_index === section)
			.map((b) => b.bin_index)
			.sort((a, b) => a - b);
	}

	// -- Manual benchmark: pick start/target bins and which layers' gates close/open --

	let startLayer = $state(0);
	let startSection = $state(0);
	let startBin = $state(0);
	let targetLayer = $state(0);
	let targetSection = $state(0);
	let targetBin = $state(0);
	let closeLayer = $state(0);
	let openLayer = $state(0);

	$effect(() => {
		const opts = sectionsFor(startLayer);
		if (!opts.includes(startSection)) startSection = opts[0] ?? 0;
	});
	$effect(() => {
		const opts = binsFor(startLayer, startSection);
		if (!opts.includes(startBin)) startBin = opts[0] ?? 0;
	});
	$effect(() => {
		const opts = sectionsFor(targetLayer);
		if (!opts.includes(targetSection)) targetSection = opts[0] ?? 0;
	});
	$effect(() => {
		const opts = binsFor(targetLayer, targetSection);
		if (!opts.includes(targetBin)) targetBin = opts[0] ?? 0;
	});

	let preparing = $state(false);
	let running = $state(false);
	let runError = $state<string | null>(null);
	type BenchmarkResult = { totalMs: number; chuteMs: number; closeMs: number; openMs: number };
	let lastResult = $state<BenchmarkResult | null>(null);

	async function moveToBinAndWait(
		layer: number,
		section: number,
		bin: number,
		skipServo = true
	): Promise<void> {
		const res = await fetch(`${backendBase()}/api/bins/move-to`, {
			method: 'POST',
			headers: { 'Content-Type': 'application/json' },
			body: JSON.stringify({
				layer_index: layer,
				section_index: section,
				bin_index: bin,
				skip_servo: skipServo
			})
		});
		if (!res.ok) {
			const detail = await res.json().catch(() => null);
			throw new Error(detail?.detail ?? `HTTP ${res.status}`);
		}
		await waitForChuteStopped();
	}

	async function waitForChuteStopped(timeoutMs = 20000): Promise<void> {
		const deadline = Date.now() + timeoutMs;
		await sleep(150); // let the move actually start before polling
		while (Date.now() < deadline) {
			const res = await fetch(`${backendBase()}/api/hardware-config/chute/live`);
			if (res.ok) {
				const data = await res.json();
				if (data?.stepper_stopped === true) return;
			}
			await sleep(120);
		}
		throw new Error('Timed out waiting for the chute to stop.');
	}

	async function setGate(layer: number, state: 'open' | 'close'): Promise<void> {
		const res = await fetch(`${backendBase()}/api/hardware-config/servo/layers/${layer}/set-gate`, {
			method: 'POST',
			headers: { 'Content-Type': 'application/json' },
			body: JSON.stringify({ state })
		});
		if (!res.ok) {
			const detail = await res.json().catch(() => null);
			throw new Error(detail?.detail ?? `HTTP ${res.status}`);
		}
	}

	const sameCloseOpenLayer = $derived(closeLayer === openLayer);

	async function runManualBenchmark() {
		if (sameCloseOpenLayer) return;
		running = true;
		preparing = true;
		runError = null;
		lastResult = null;
		try {
			// Setup (unmeasured): get into position, and into the "before" gate
			// state, so the timed phase always has a real open->close and
			// close->open transition to measure.
			await moveToBinAndWait(startLayer, startSection, startBin);
			await setGate(closeLayer, 'open');
			await setGate(openLayer, 'close');
			preparing = false;

			const t0 = performance.now();
			let chuteMs = 0;
			let closeMs = 0;
			let openMs = 0;
			await Promise.all([
				moveToBinAndWait(targetLayer, targetSection, targetBin).then(() => {
					chuteMs = performance.now() - t0;
				}),
				setGate(closeLayer, 'close').then(() => {
					closeMs = performance.now() - t0;
				}),
				setGate(openLayer, 'open').then(() => {
					openMs = performance.now() - t0;
				})
			]);
			lastResult = { totalMs: performance.now() - t0, chuteMs, closeMs, openMs };
		} catch (e: any) {
			runError = e?.message ?? 'Benchmark failed.';
		} finally {
			preparing = false;
			running = false;
		}
	}

	// -- Random test: repeatedly drive to a random bin in a random layer --

	let randomRuns = $state(20);
	let randomRunning = $state(false);
	let randomStopRequested = false;
	let randomProgress = $state(0);
	let randomTimesMs = $state<number[]>([]);
	let randomError = $state<string | null>(null);

	function pickRandomBin(): { layer: number; section: number; bin: number } | null {
		const candidates = layerLayouts.filter((l) => l.bins.length > 0);
		if (candidates.length === 0) return null;
		const layer = candidates[Math.floor(Math.random() * candidates.length)];
		const bin = layer.bins[Math.floor(Math.random() * layer.bins.length)];
		return { layer: layer.layer_index, section: bin.section_index, bin: bin.bin_index };
	}

	async function runRandomTest() {
		randomRunning = true;
		randomStopRequested = false;
		randomError = null;
		randomTimesMs = [];
		randomProgress = 0;
		try {
			for (let i = 0; i < randomRuns; i++) {
				if (randomStopRequested) break;
				const target = pickRandomBin();
				if (target === null) throw new Error('No bins are configured.');
				const t0 = performance.now();
				// skipServo=false: let /bins/move-to manage gates itself (close
				// whatever's open, open the target layer) the same way real
				// sorting does, so each run's time reflects chute + gate
				// switching together, not just the chute alone.
				await moveToBinAndWait(target.layer, target.section, target.bin, false);
				randomTimesMs = [...randomTimesMs, performance.now() - t0];
				randomProgress = i + 1;
			}
		} catch (e: any) {
			randomError = e?.message ?? 'Benchmark failed.';
		} finally {
			randomRunning = false;
		}
	}

	function stopRandomTest() {
		randomStopRequested = true;
	}

	const randomStats = $derived.by(() => {
		if (randomTimesMs.length === 0) return null;
		const sum = randomTimesMs.reduce((a, b) => a + b, 0);
		return {
			count: randomTimesMs.length,
			min: Math.min(...randomTimesMs),
			max: Math.max(...randomTimesMs),
			avg: sum / randomTimesMs.length
		};
	});

	function fmtMs(ms: number): string {
		return `${Math.round(ms)} ms`;
	}

	function layerOptions() {
		return layerLayouts.map((l) => ({ value: String(l.layer_index), label: `Layer ${l.layer_index + 1}` }));
	}

	onMount(() => void loadLayout());
</script>

<Panel
	title="Bin-to-bin benchmark"
	description="Move the chute from one bin to another while closing one layer's gate and opening another's, timed as one operation."
>
	{#if layersLoading}
		<div class="flex items-center gap-2 px-(--pad-panel) pb-(--pad-panel) text-sm text-ink-muted">
			<Spinner size={14} />
		</div>
	{:else if layoutError}
		<div class="px-(--pad-panel) pb-(--pad-panel)">
			<Alert tone="danger">{layoutError}</Alert>
		</div>
	{:else}
		<div class="flex flex-col gap-4 px-(--pad-panel) pb-(--pad-panel)">
			<div class="grid grid-cols-1 gap-x-6 gap-y-3 sm:grid-cols-2">
				<div class="flex flex-col gap-2 rounded-item bg-well p-3">
					<span class="text-sm font-medium text-ink">Start bin</span>
					<div class="flex flex-wrap gap-2">
						<Select
							size="sm"
							value={String(startLayer)}
							onchange={(v) => (startLayer = Number(v))}
							options={layerOptions()}
						/>
						<Select
							size="sm"
							value={String(startSection)}
							onchange={(v) => (startSection = Number(v))}
							options={sectionsFor(startLayer).map((s) => ({ value: String(s), label: `Section ${s + 1}` }))}
						/>
						<Select
							size="sm"
							value={String(startBin)}
							onchange={(v) => (startBin = Number(v))}
							options={binsFor(startLayer, startSection).map((b) => ({ value: String(b), label: `Bin ${b + 1}` }))}
						/>
					</div>
				</div>
				<div class="flex flex-col gap-2 rounded-item bg-well p-3">
					<span class="text-sm font-medium text-ink">Target bin</span>
					<div class="flex flex-wrap gap-2">
						<Select
							size="sm"
							value={String(targetLayer)}
							onchange={(v) => (targetLayer = Number(v))}
							options={layerOptions()}
						/>
						<Select
							size="sm"
							value={String(targetSection)}
							onchange={(v) => (targetSection = Number(v))}
							options={sectionsFor(targetLayer).map((s) => ({ value: String(s), label: `Section ${s + 1}` }))}
						/>
						<Select
							size="sm"
							value={String(targetBin)}
							onchange={(v) => (targetBin = Number(v))}
							options={binsFor(targetLayer, targetSection).map((b) => ({ value: String(b), label: `Bin ${b + 1}` }))}
						/>
					</div>
				</div>
				<Field label="Close gate" for="benchmark-close-layer" info="Which layer's gate closes during the timed move.">
					<Select
						id="benchmark-close-layer"
						size="sm"
						value={String(closeLayer)}
						onchange={(v) => (closeLayer = Number(v))}
						options={layerOptions()}
					/>
				</Field>
				<Field label="Open gate" for="benchmark-open-layer" info="Which layer's gate opens during the timed move.">
					<Select
						id="benchmark-open-layer"
						size="sm"
						value={String(openLayer)}
						onchange={(v) => (openLayer = Number(v))}
						options={layerOptions()}
					/>
				</Field>
			</div>

			{#if sameCloseOpenLayer}
				<Alert tone="warning"
					>Pick two different layers to close and open — the same gate can't be measured opening and
					closing at once.</Alert
				>
			{/if}

			<div class="flex items-center gap-3">
				<Button
					variant="primary"
					loading={running}
					disabled={sameCloseOpenLayer}
					onclick={() => void runManualBenchmark()}
				>
					Run benchmark
				</Button>
				{#if preparing}
					<span class="flex items-center gap-1.5 text-sm text-ink-muted">
						<Spinner size={14} /> Preparing (not timed)…
					</span>
				{/if}
			</div>

			{#if runError}
				<Alert tone="danger">{runError}</Alert>
			{/if}

			{#if lastResult}
				<div class="grid grid-cols-2 gap-3 sm:grid-cols-4">
					<Stat label="Total" value={fmtMs(lastResult.totalMs)} />
					<Stat label="Chute" value={fmtMs(lastResult.chuteMs)} />
					<Stat label="Close" value={fmtMs(lastResult.closeMs)} />
					<Stat label="Open" value={fmtMs(lastResult.openMs)} />
				</div>
			{/if}
		</div>
	{/if}
</Panel>

<Panel
	title="Random test"
	description="Drives to random bins in random layers, one after another, timing each move and reporting the spread."
>
	<div class="flex flex-col gap-4 px-(--pad-panel) pb-(--pad-panel)">
		<div class="flex flex-wrap items-end gap-3">
			<Field label="Runs" for="benchmark-random-runs">
				<Input
					id="benchmark-random-runs"
					type="number"
					size="sm"
					min={1}
					step={1}
					value={String(randomRuns)}
					disabled={randomRunning}
					oninput={(e) => (randomRuns = Math.max(1, Number((e.currentTarget as HTMLInputElement).value) || 1))}
					class="w-24"
				/>
			</Field>
			<Button
				variant="primary"
				loading={randomRunning}
				disabled={layersLoading || Boolean(layoutError)}
				onclick={() => void runRandomTest()}
			>
				Start
			</Button>
			{#if randomRunning}
				<Button variant="danger" onclick={stopRandomTest}>Stop</Button>
				<Badge tone="info">Run {randomProgress} / {randomRuns}</Badge>
			{/if}
		</div>

		{#if randomError}
			<Alert tone="danger">{randomError}</Alert>
		{/if}

		{#if randomStats}
			<div class="grid grid-cols-2 gap-3 sm:grid-cols-4">
				<Stat label="Runs" value={String(randomStats.count)} />
				<Stat label="Fastest" value={fmtMs(randomStats.min)} />
				<Stat label="Slowest" value={fmtMs(randomStats.max)} />
				<Stat label="Average" value={fmtMs(randomStats.avg)} />
			</div>
			<div class="max-h-48 overflow-y-auto rounded-item bg-well p-2">
				<div class="flex flex-wrap gap-1.5">
					{#each randomTimesMs as ms, i (i)}
						<Badge>Run {i + 1}: {fmtMs(ms)}</Badge>
					{/each}
				</div>
			</div>
		{/if}
	</div>
</Panel>
