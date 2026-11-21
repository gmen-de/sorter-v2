<script lang="ts">
	import { onMount } from 'svelte';
	import Alert from '$lib/components/ui/Alert.svelte';
	import Badge from '$lib/components/ui/Badge.svelte';
	import Button from '$lib/components/ui/Button.svelte';
	import Panel from '$lib/components/ui/Panel.svelte';
	import Spinner from '$lib/components/ui/Spinner.svelte';
	import { getBackendHttpBase, machineHttpBaseUrlFromWsUrl } from '$lib/backend';
	import { getMachineContext } from '$lib/machines/context';

	type GateState = 'open' | 'closed' | 'blocked' | 'unknown' | 'error' | undefined;

	const machine = getMachineContext();

	function backendBase(): string {
		return machineHttpBaseUrlFromWsUrl(machine.machine?.url) ?? getBackendHttpBase();
	}

	let loading = $state(true);
	let backend = $state<'pca9685' | 'waveshare_ble' | null>(null);
	let device = $state('');

	let connected = $state(false);
	let knownGates = $state<number[]>([]);
	let lastError = $state<string | null>(null);
	let statusLoading = $state(false);
	let learning = $state(false);
	let resettingAll = $state(false);
	let statusMsg = $state('');
	let busyByGate = $state<Record<number, string>>({});
	let stateByGate = $state<Record<number, GateState>>({});

	function setBusy(gate: number, kind: string | null) {
		if (kind === null) {
			const next = { ...busyByGate };
			delete next[gate];
			busyByGate = next;
		} else {
			busyByGate = { ...busyByGate, [gate]: kind };
		}
	}

	async function loadBridgeSettings() {
		try {
			const res = await fetch(`${backendBase()}/api/hardware-config/servo`);
			if (!res.ok) throw new Error(await res.text());
			const data = await res.json();
			backend = data.backend ?? null;
			device = data.device ?? '';
		} catch {
			backend = null;
		} finally {
			loading = false;
		}
	}

	async function loadStatus() {
		if (backend !== 'waveshare_ble') return;
		statusLoading = true;
		try {
			const url = new URL(`${backendBase()}/api/hardware-config/waveshare-ble/status`);
			if (device.trim()) url.searchParams.set('device', device.trim());
			const res = await fetch(url.toString());
			if (!res.ok) throw new Error(await res.text());
			const payload = await res.json();
			connected = Boolean(payload?.connected);
			knownGates = Array.isArray(payload?.gates)
				? payload.gates.filter((g: unknown): g is number => typeof g === 'number')
				: [];
			lastError = typeof payload?.last_error === 'string' ? payload.last_error : null;
		} catch (e: any) {
			lastError = e?.message ?? 'Failed to read bridge status.';
			connected = false;
		} finally {
			statusLoading = false;
		}
	}

	async function learn() {
		learning = true;
		lastError = null;
		statusMsg = '';
		try {
			const res = await fetch(`${backendBase()}/api/hardware-config/waveshare-ble/learn`, {
				method: 'POST'
			});
			if (!res.ok) throw new Error(await res.text());
			const payload = await res.json();
			statusMsg = payload?.message ?? 'Servo learned.';
			await loadStatus();
		} catch (e: any) {
			lastError = e?.message ?? 'LEARN failed.';
		} finally {
			learning = false;
		}
	}

	async function resetAll() {
		resettingAll = true;
		lastError = null;
		statusMsg = '';
		try {
			const res = await fetch(`${backendBase()}/api/hardware-config/waveshare-ble/reset-all`, {
				method: 'POST'
			});
			if (!res.ok) throw new Error(await res.text());
			const payload = await res.json();
			statusMsg = payload?.message ?? 'Servos reset.';
			stateByGate = {};
			await loadStatus();
		} catch (e: any) {
			lastError = e?.message ?? 'Factory reset failed.';
		} finally {
			resettingAll = false;
		}
	}

	async function moveGate(gate: number, position: 'open' | 'close') {
		setBusy(gate, position === 'open' ? 'opening' : 'closing');
		lastError = null;
		try {
			const res = await fetch(`${backendBase()}/api/hardware-config/waveshare-ble/gates/${gate}/move`, {
				method: 'POST',
				headers: { 'Content-Type': 'application/json' },
				body: JSON.stringify({ position })
			});
			if (!res.ok) throw new Error(await res.text());
			const payload = await res.json();
			const state = String(payload?.state ?? '').toLowerCase();
			stateByGate = {
				...stateByGate,
				[gate]: (['open', 'closed', 'blocked'].includes(state) ? state : 'unknown') as GateState
			};
		} catch (e: any) {
			lastError = e?.message ?? `Failed to move gate ${gate}.`;
			stateByGate = { ...stateByGate, [gate]: 'error' };
		} finally {
			setBusy(gate, null);
		}
	}

	async function calibrateGate(gate: number) {
		setBusy(gate, 'calibrating');
		lastError = null;
		statusMsg = '';
		try {
			const res = await fetch(
				`${backendBase()}/api/hardware-config/waveshare-ble/gates/${gate}/calibrate`,
				{ method: 'POST' }
			);
			if (!res.ok) throw new Error(await res.text());
			const payload = await res.json();
			statusMsg = payload?.message ?? `Gate ${gate} calibrated.`;
		} catch (e: any) {
			lastError = e?.message ?? `Failed to calibrate gate ${gate}.`;
		} finally {
			setBusy(gate, null);
		}
	}

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

	onMount(async () => {
		await loadBridgeSettings();
		if (backend === 'waveshare_ble') void loadStatus();
	});
</script>

{#if loading}
	<div class="flex items-center gap-2 p-4 text-sm text-ink-muted">
		<Spinner size={14} />
	</div>
{:else if backend !== 'waveshare_ble'}
	<Alert tone="info">Diagnostics are only available for the Waveshare BLE backend. Switch to it in Settings above to use this.</Alert>
{:else if !device.trim()}
	<Alert tone="info">No BLE device address is saved yet. Set one in Settings above first.</Alert>
{:else}
	<Panel title="Gate test" description="Drive the Waveshare BLE bridge's gates directly to test open/close/calibrate — independent of the sorter's normal layer-gate mapping.">
		{#snippet actions()}
			<div class="flex items-center gap-2">
				<Badge tone={connected ? 'success' : 'neutral'} dot>
					{connected ? 'Connected' : 'Not connected'}
				</Badge>
				<Button size="sm" loading={statusLoading} onclick={() => void loadStatus()}>
					Rescan
				</Button>
			</div>
		{/snippet}

		{#if lastError}
			<div class="px-(--pad-panel) pb-2">
				<Alert tone="danger">{lastError}</Alert>
			</div>
		{/if}
		{#if statusMsg}
			<div class="px-(--pad-panel) pb-2 text-sm text-success-ink">{statusMsg}</div>
		{/if}
	</Panel>

	<Panel
		title="Known gates"
		description={`${knownGates.length} gate(s) reachable on the bus.`}
		flush
	>
		{#if knownGates.length === 0}
			<p class="px-(--pad-panel) pb-4 text-sm text-ink-muted">
				No gates found yet. Press Rescan, or Learn next servo below if none have been onboarded.
			</p>
		{:else}
			<div class="divide-y divide-line">
				{#each knownGates as gate (gate)}
					{@const busy = busyByGate[gate]}
					<div class="flex flex-wrap items-center gap-3 px-(--pad-panel) py-3">
						<span class="w-20 shrink-0 text-sm font-medium text-ink">Gate {gate}</span>
						<Badge tone={badgeTone(stateByGate[gate])}>{badgeLabel(stateByGate[gate])}</Badge>
						<div class="ml-auto flex items-center gap-2">
							<Button
								size="sm"
								loading={busy === 'opening'}
								disabled={Boolean(busy)}
								onclick={() => void moveGate(gate, 'open')}
							>
								Open
							</Button>
							<Button
								size="sm"
								loading={busy === 'closing'}
								disabled={Boolean(busy)}
								onclick={() => void moveGate(gate, 'close')}
							>
								Close
							</Button>
							<Button
								size="sm"
								loading={busy === 'calibrating'}
								disabled={Boolean(busy)}
								onclick={() => void calibrateGate(gate)}
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
				<Button loading={learning} onclick={() => void learn()}>Learn next servo</Button>
				<Button variant="danger" loading={resettingAll} onclick={() => void resetAll()}>
					Factory-reset all
				</Button>
				<p class="text-sm text-ink-muted">
					Learn next servo onboards the next factory-default servo found at ID 1 and calibrates
					it. Factory-reset all clears every reachable servo back to ID 1 so onboarding can start
					over.
				</p>
			</div>
		{/snippet}
	</Panel>
{/if}
