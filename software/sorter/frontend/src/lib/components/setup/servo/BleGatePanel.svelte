<script lang="ts">
	import Alert from '$lib/components/ui/Alert.svelte';
	import Badge from '$lib/components/ui/Badge.svelte';
	import Button from '$lib/components/ui/Button.svelte';
	import Field from '$lib/components/ui/Field.svelte';
	import Input from '$lib/components/ui/Input.svelte';
	import Panel from '$lib/components/ui/Panel.svelte';
	import Select from '$lib/components/ui/Select.svelte';
	import Switch from '$lib/components/ui/Switch.svelte';

	type GateState = 'open' | 'closed' | 'blocked' | 'unknown' | 'error' | undefined;

	let {
		device = $bindable(),
		connected,
		knownGates,
		lastError,
		statusLoading,
		learning,
		resettingAll,
		busyByGate,
		stateByGate,
		layerByGate,
		invertByLayer,
		unassignedLayers,
		onLoadStatus,
		onLearn,
		onResetAll,
		onAssignLayer,
		onToggleInvert,
		onMoveGate,
		onCalibrateGate
	}: {
		device: string;
		connected: boolean;
		knownGates: number[];
		lastError: string | null;
		statusLoading: boolean;
		learning: boolean;
		resettingAll: boolean;
		busyByGate: Record<number, string>;
		stateByGate: Record<number, GateState>;
		layerByGate: Record<number, number>;
		invertByLayer: Record<number, boolean>;
		unassignedLayers: (currentLayer: number) => number[];
		onLoadStatus: () => void;
		onLearn: () => void;
		onResetAll: () => void;
		onAssignLayer: (gate: number, layer: number) => void;
		onToggleInvert: (layer: number) => void;
		onMoveGate: (gate: number, position: 'open' | 'close') => void;
		onCalibrateGate: (gate: number) => void;
	} = $props();

	function badgeTone(state: GateState): 'neutral' | 'success' | 'info' | 'danger' {
		if (state === 'open') return 'success';
		if (state === 'closed') return 'info';
		if (state === 'blocked' || state === 'error') return 'danger';
		return 'neutral';
	}

	function badgeLabel(state: GateState): string {
		if (state === 'open') return 'Open';
		if (state === 'closed') return 'Closed';
		if (state === 'blocked') return 'Blocked';
		if (state === 'error') return 'Error';
		if (state === 'unknown') return 'Unknown';
		return 'Untested';
	}
</script>

<Panel title="ESP32 BLE gate bridge">
	{#snippet actions()}
		<Badge tone={connected ? 'success' : 'neutral'} dot>
			{connected ? 'Connected' : 'Not connected'}
		</Badge>
	{/snippet}

	<div class="flex flex-wrap items-end gap-2">
		<Field label="BLE device address" for="ble-device" class="min-w-0 flex-[2_1_16rem]">
			<Input id="ble-device" bind:value={device} placeholder="AA:BB:CC:DD:EE:FF" />
		</Field>
		<Button loading={statusLoading} onclick={onLoadStatus}>Rescan</Button>
	</div>

	{#if lastError}
		<Alert tone="danger">{lastError}</Alert>
	{/if}
</Panel>

<Panel title="Gates on the bridge" description="{knownGates.length} reachable right now" flush>
	{#if knownGates.length === 0}
		<p class="px-(--pad-panel) pb-4 text-sm text-ink-muted">
			No gates reachable yet. Connect one new servo at a time and press "Learn next servo" below —
			it gets the next gate number automatically and calibrates itself.
		</p>
	{:else}
		<div class="divide-y divide-line">
			{#each knownGates as gate (gate)}
				{@const layer = layerByGate[gate] ?? 0}
				{@const busy = busyByGate[gate]}
				<div class="flex flex-wrap items-center gap-3 px-(--pad-panel) py-3">
					<span class="w-16 shrink-0 text-sm font-medium text-ink">Gate {gate}</span>
					<Badge tone={badgeTone(stateByGate[gate])}>{badgeLabel(stateByGate[gate])}</Badge>

					<Select
						label="Storage layer for gate {gate}"
						size="sm"
						class="min-w-36"
						value={String(layer)}
						options={[
							{ value: '0', label: 'Unassigned' },
							...unassignedLayers(layer).map((l) => ({ value: String(l), label: `Layer ${l}` }))
						]}
						onchange={(value) => onAssignLayer(gate, Number(value))}
					/>

					{#if layer > 0}
						<label class="flex items-center gap-2 text-sm text-ink-muted">
							<Switch
								checked={Boolean(invertByLayer[layer])}
								onchange={() => onToggleInvert(layer)}
							/>
							Inverted
						</label>
					{/if}

					<div class="ml-auto flex items-center gap-2">
						<Button
							size="sm"
							loading={busy === 'opening'}
							disabled={Boolean(busy)}
							onclick={() => onMoveGate(gate, 'open')}
						>
							Open
						</Button>
						<Button
							size="sm"
							loading={busy === 'closing'}
							disabled={Boolean(busy)}
							onclick={() => onMoveGate(gate, 'close')}
						>
							Close
						</Button>
						<Button
							size="sm"
							loading={busy === 'calibrating'}
							disabled={Boolean(busy)}
							onclick={() => onCalibrateGate(gate)}
						>
							Calibrate
						</Button>
					</div>
				</div>
			{/each}
		</div>
	{/if}
	{#snippet footer()}
		<div class="flex w-full flex-wrap items-center gap-2">
			<Button loading={learning} onclick={onLearn}>Learn next servo</Button>
			<Button variant="danger" loading={resettingAll} onclick={onResetAll}>
				Factory-reset all
			</Button>
			<p class="text-sm text-ink-muted">
				Connect one new servo at a time, then "Learn next servo" — it gets the next gate number (11,
				12, 13, …) and calibrates itself. "Factory-reset all" clears every reachable servo's ID and
				calibration so onboarding can start over.
			</p>
		</div>
	{/snippet}
</Panel>
