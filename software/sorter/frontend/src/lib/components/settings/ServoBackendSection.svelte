<script lang="ts">
	import { onMount } from 'svelte';
	import Panel from '$lib/components/ui/Panel.svelte';
	import Field from '$lib/components/ui/Field.svelte';
	import Select from '$lib/components/ui/Select.svelte';
	import Input from '$lib/components/ui/Input.svelte';
	import Button from '$lib/components/ui/Button.svelte';
	import Spinner from '$lib/components/ui/Spinner.svelte';
	import Alert from '$lib/components/ui/Alert.svelte';
	import Switch from '$lib/components/ui/Switch.svelte';
	import { getBackendHttpBase, machineHttpBaseUrlFromWsUrl } from '$lib/backend';
	import { getMachineContext } from '$lib/machines/context';

	type ServoChannel = { id: number | null; invert: boolean };
	type ServoSettings = {
		backend: 'pca9685' | 'waveshare_ble';
		device: string | null;
		channels: ServoChannel[];
	};

	const machine = getMachineContext();

	let loading = $state(true);
	let loadError = $state<string | null>(null);
	let settings = $state<ServoSettings | null>(null);
	let backend = $state<'pca9685' | 'waveshare_ble'>('pca9685');
	let device = $state('');

	let saveStatus = $state<'idle' | 'saving' | 'saved' | 'error'>('idle');
	let saveMessage = $state('');

	type ScannedDevice = { address: string; name: string; rssi: number | null; is_sorter_servo: boolean };
	let scanning = $state(false);
	let scanError = $state<string | null>(null);
	let scannedDevices = $state<ScannedDevice[]>([]);
	let hasScanned = $state(false);

	async function scan() {
		scanning = true;
		scanError = null;
		scannedDevices = [];
		hasScanned = true;
		try {
			const res = await fetch(`${backendBase()}/api/hardware-config/waveshare-ble/scan`);
			if (!res.ok) throw new Error(await res.text());
			const data = await res.json();
			scannedDevices = data.devices ?? [];
		} catch (e: any) {
			scanError = e?.message ?? 'Scan failed.';
		} finally {
			scanning = false;
		}
	}

	function pickDevice(address: string) {
		device = address;
		saveStatus = 'idle';
	}

	let demoMode = $state(false);
	let demoLoading = $state(false);
	let demoError = $state<string | null>(null);

	async function loadDemoMode() {
		if (!device.trim()) return;
		demoError = null;
		try {
			const res = await fetch(
				`${backendBase()}/api/hardware-config/waveshare-ble/demo?device=${encodeURIComponent(device.trim())}`
			);
			if (!res.ok) throw new Error(await res.text());
			const data = await res.json();
			demoMode = Boolean(data.demo_mode);
		} catch (e: any) {
			demoError = e?.message ?? 'Could not read demo mode.';
		}
	}

	async function toggleDemoMode(enabled: boolean) {
		demoLoading = true;
		demoError = null;
		try {
			const res = await fetch(
				`${backendBase()}/api/hardware-config/waveshare-ble/demo?device=${encodeURIComponent(device.trim())}`,
				{
					method: 'POST',
					headers: { 'Content-Type': 'application/json' },
					body: JSON.stringify({ enabled })
				}
			);
			if (!res.ok) throw new Error(await res.text());
			const data = await res.json();
			demoMode = Boolean(data.demo_mode);
		} catch (e: any) {
			demoError = e?.message ?? 'Could not change demo mode.';
			demoMode = !enabled;
		} finally {
			demoLoading = false;
		}
	}

	function backendBase(): string {
		return machineHttpBaseUrlFromWsUrl(machine.machine?.url) ?? getBackendHttpBase();
	}

	async function load() {
		loading = true;
		loadError = null;
		try {
			const res = await fetch(`${backendBase()}/api/hardware-config/servo`);
			if (!res.ok) throw new Error(await res.text());
			const data = (await res.json()) as ServoSettings;
			settings = data;
			backend = data.backend;
			device = data.device ?? '';
			if (backend === 'waveshare_ble') void loadDemoMode();
		} catch (e: any) {
			loadError = e?.message ?? 'Failed to reach the servo settings.';
		} finally {
			loading = false;
		}
	}

	async function save() {
		if (!settings) return;
		saveStatus = 'saving';
		saveMessage = '';
		try {
			const res = await fetch(`${backendBase()}/api/hardware-config/servo`, {
				method: 'POST',
				headers: { 'Content-Type': 'application/json' },
				body: JSON.stringify({
					backend,
					device: backend === 'waveshare_ble' ? device.trim() : null,
					channels: settings.channels
				})
			});
			if (!res.ok) throw new Error(await res.text());
			const data = await res.json();
			settings = data.settings;
			backend = data.settings.backend;
			device = data.settings.device ?? '';
			saveStatus = 'saved';
			saveMessage = data.message ?? 'Saved.';
			if (backend === 'waveshare_ble') void loadDemoMode();
		} catch (e: any) {
			saveStatus = 'error';
			saveMessage = e?.message ?? 'Save failed.';
		}
	}

	onMount(() => void load());
</script>

<Panel
	title="Servo control"
	description="Where layer-gate servo commands go: a control board's PCA9685 channels, or the ESP32 BLE gate bridge."
	flush
>
	{#snippet actions()}
		{#if saveStatus === 'saving'}
			<span class="flex items-center gap-2 text-sm text-ink-muted"><Spinner size={14} /> Saving</span>
		{:else if saveStatus === 'saved'}
			<span class="text-sm text-success-ink">{saveMessage}</span>
		{:else if saveStatus === 'error'}
			<span class="text-sm text-danger-ink" title={saveMessage}>Not saved</span>
		{/if}
	{/snippet}
	<div class="flex flex-col gap-4 px-(--pad-panel) pb-(--pad-panel)">
		{#if loadError}
			<Alert tone="danger">{loadError}</Alert>
		{:else if loading}
			<div class="flex items-center gap-2 text-sm text-ink-muted"><Spinner size={14} /> Loading</div>
		{:else}
			<Field label="Backend" for="servo-backend" help="Changing this needs the hardware re-homed afterward.">
				<Select
					id="servo-backend"
					value={backend}
					options={[
						{ value: 'pca9685', label: 'Control board (PCA9685)' },
						{ value: 'waveshare_ble', label: 'BLE servo (ESP32 gate bridge)' }
					]}
					onchange={(v) => {
						const next = (v || 'pca9685') as 'pca9685' | 'waveshare_ble';
						if (next !== backend && settings) {
							// Each backend numbers channels differently (PCA9685 channel
							// 0-based, BLE gate 1-based) — the old ids are meaningless (and
							// often invalid) under the other backend, so remap them along
							// with the switch instead of sending stale ones on save.
							settings.channels = settings.channels.map((channel, index) => ({
								...channel,
								id: next === 'waveshare_ble' ? index + 1 : index
							}));
						}
						backend = next;
						saveStatus = 'idle';
					}}
				/>
			</Field>
			{#if backend === 'waveshare_ble'}
				<Field
					label="BLE device"
					for="servo-ble-device"
					help="The BLE address of the esp32_ble_servo_bridge board, e.g. AA:BB:CC:DD:EE:FF."
				>
					<div class="flex gap-2">
						<Input
							id="servo-ble-device"
							type="text"
							placeholder="AA:BB:CC:DD:EE:FF"
							value={device}
							oninput={(e) => {
								device = (e.currentTarget as HTMLInputElement).value;
								saveStatus = 'idle';
							}}
						/>
						<Button variant="secondary" disabled={scanning} onclick={() => void scan()}>
							{#if scanning}
								<span class="flex items-center gap-2"><Spinner size={14} /> Scanning</span>
							{:else}
								Scan
							{/if}
						</Button>
					</div>
				</Field>
				{#if scanError}
					<Alert tone="danger">{scanError}</Alert>
				{:else if scannedDevices.length > 0}
					<div class="flex flex-col gap-1 rounded border border-stroke-muted p-2">
						{#each scannedDevices as found (found.address)}
							<button
								type="button"
								class="flex items-center justify-between gap-3 rounded px-2 py-1 text-left text-sm hover:bg-surface-muted"
								class:text-success-ink={found.is_sorter_servo}
								onclick={() => pickDevice(found.address)}
							>
								<span class="font-mono">{found.address}</span>
								<span class="text-ink-muted">{found.name || '(unnamed)'}{found.is_sorter_servo ? ' — SORTER-SERVO-BLE' : ''}</span>
								<span class="text-ink-muted">{found.rssi ?? ''} dBm</span>
							</button>
						{/each}
					</div>
				{:else if hasScanned && !scanning}
					<p class="text-sm text-ink-muted">No BLE devices found nearby.</p>
				{/if}
				<Field
					label="Demo mode"
					for="servo-ble-demo"
					help="The bridge board answers every gate command as if a real servo moved, after the same delay a real move takes — without driving the servo bus. Applies immediately, persists across reboots."
				>
					<div class="flex items-center gap-2">
						<Switch
							checked={demoMode}
							disabled={demoLoading || !device.trim()}
							label="Demo mode"
							onchange={(checked) => void toggleDemoMode(checked)}
						/>
						{#if demoLoading}<Spinner size={14} />{/if}
					</div>
				</Field>
				{#if demoError}
					<Alert tone="danger">{demoError}</Alert>
				{/if}
			{/if}
			<div>
				<Button
					variant="primary"
					disabled={saveStatus === 'saving' || (backend === 'waveshare_ble' && !device.trim())}
					onclick={() => void save()}
				>
					Save
				</Button>
			</div>
		{/if}
	</div>
</Panel>
