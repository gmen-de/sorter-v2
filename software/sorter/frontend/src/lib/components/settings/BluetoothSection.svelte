<script lang="ts">
	import { onMount } from 'svelte';
	import { machineHttpBaseUrlFromWsUrl, getBackendHttpBase } from '$lib/backend';
	import { getMachineContext } from '$lib/machines/context';
	import { remember, remembered } from '$lib/settings/networkingCache';
	import Button from '$lib/components/ui/Button.svelte';
	import Alert from '$lib/components/ui/Alert.svelte';
	import Spinner from '$lib/components/ui/Spinner.svelte';
	import Bluetooth from '@lucide/svelte/icons/bluetooth';
	import Check from '@lucide/svelte/icons/check';
	import RefreshCw from '@lucide/svelte/icons/refresh-cw';
	import X from '@lucide/svelte/icons/x';

	const machine = getMachineContext();

	type BtDevice = {
		address: string;
		name: string;
		paired: boolean;
		trusted: boolean;
		connected: boolean;
	};
	type BtStatus = {
		available: boolean;
		devices: BtDevice[];
		error?: string;
	};

	const BRIDGE_NAME_PREFIX = 'SC15-Bridge';

	let status = $state<BtStatus | null>(null);
	let loadError = $state<string | null>(null);
	let scanning = $state(false);
	let busyAddress = $state<string | null>(null);
	let actionError = $state<string | null>(null);

	function httpBase(): string {
		return machineHttpBaseUrlFromWsUrl(machine.machine?.url) ?? getBackendHttpBase();
	}

	function applyStatus(s: BtStatus) {
		status = remember(`${httpBase()}/bluetooth`, s);
	}

	async function loadStatus() {
		loadError = null;
		try {
			const res = await fetch(`${httpBase()}/api/bluetooth/status`);
			if (!res.ok) throw new Error(await res.text());
			applyStatus(await res.json());
		} catch (e: any) {
			loadError = e.message ?? 'Failed to load Bluetooth status';
		}
	}

	async function rescan() {
		scanning = true;
		loadError = null;
		try {
			const res = await fetch(`${httpBase()}/api/bluetooth/scan`, { method: 'POST' });
			if (!res.ok) throw new Error(await res.text());
			applyStatus(await res.json());
		} catch (e: any) {
			loadError = e.message ?? 'Scan failed';
		} finally {
			scanning = false;
		}
	}

	async function pair(dev: BtDevice) {
		busyAddress = dev.address;
		actionError = null;
		try {
			const res = await fetch(`${httpBase()}/api/bluetooth/pair`, {
				method: 'POST',
				headers: { 'Content-Type': 'application/json' },
				body: JSON.stringify({ address: dev.address })
			});
			const data = await res.json();
			if (!data.ok) {
				actionError = data.error ?? 'Pairing failed';
			} else {
				await loadStatus();
			}
		} catch (e: any) {
			actionError = e.message ?? 'Pairing failed';
		} finally {
			busyAddress = null;
		}
	}

	async function forget(dev: BtDevice) {
		busyAddress = dev.address;
		actionError = null;
		try {
			const res = await fetch(`${httpBase()}/api/bluetooth/remove`, {
				method: 'POST',
				headers: { 'Content-Type': 'application/json' },
				body: JSON.stringify({ address: dev.address })
			});
			const data = await res.json();
			if (!data.ok) {
				actionError = data.error ?? 'Removing the device failed';
			} else {
				await loadStatus();
			}
		} catch (e: any) {
			actionError = e.message ?? 'Removing the device failed';
		} finally {
			busyAddress = null;
		}
	}

	const cached = remembered<BtStatus>(`${httpBase()}/bluetooth`);
	if (cached) applyStatus(cached);

	onMount(async () => {
		await loadStatus();
	});
</script>

<div class="flex flex-col gap-4">
	{#if loadError}
		<Alert tone="danger">{loadError}</Alert>
	{/if}

	{#if status && !status.available}
		<Alert tone="warning">
			bluetoothctl isn't available on this machine, so Bluetooth devices can't be managed here.
		</Alert>
	{:else if status}
		{#if actionError}
			<Alert tone="danger">{actionError}</Alert>
		{/if}

		<div>
			<div class="flex items-center justify-between gap-3">
				<div class="label">Devices</div>
				<Button variant="ghost" size="sm" icon={RefreshCw} loading={scanning} onclick={rescan}>
					Scan
				</Button>
			</div>
			{#if status.devices.length === 0}
				<p class="mt-1 text-sm text-ink-muted">
					No devices found. Put the SC15-Bridge in pairing mode and scan.
				</p>
			{:else}
				<ul class="mt-1 divide-y divide-line">
					{#each status.devices as dev (dev.address)}
						<li class="flex items-center gap-3 py-2 text-sm">
							<Bluetooth size={16} class={dev.connected ? 'text-success-ink' : 'text-ink-faint'} />
							<div class="flex-1">
								<div class="font-medium text-ink">
									{dev.name}
									{#if dev.name.startsWith(BRIDGE_NAME_PREFIX)}
										<span class="ml-1 text-ink-muted">(chute bridge)</span>
									{/if}
								</div>
								<div class="font-mono text-xs text-ink-muted">{dev.address}</div>
							</div>
							{#if dev.connected}
								<span class="inline-flex items-center gap-1 text-success-ink">
									<Check size={16} /> Connected
								</span>
							{:else if dev.paired}
								<span class="text-ink-muted">Paired</span>
							{/if}
							{#if dev.paired}
								<Button
									variant="ghost"
									size="sm"
									icon={X}
									loading={busyAddress === dev.address}
									onclick={() => forget(dev)}
								>
									Forget
								</Button>
							{:else}
								<Button
									variant="primary"
									size="sm"
									loading={busyAddress === dev.address}
									onclick={() => pair(dev)}
								>
									Pair
								</Button>
							{/if}
						</li>
					{/each}
				</ul>
			{/if}
		</div>
	{:else}
		<div class="flex items-center gap-2 text-sm text-ink-muted"><Spinner size={14} /> Loading Bluetooth</div>
	{/if}
</div>
