<script lang="ts">
	import { onMount, onDestroy } from 'svelte';
	import { machineHttpBaseUrlFromWsUrl, getBackendHttpBase } from '$lib/backend';
	import { getMachineContext } from '$lib/machines/context';
	import Panel from '$lib/components/ui/Panel.svelte';
	import SettingRow from '$lib/components/ui/SettingRow.svelte';
	import Field from '$lib/components/ui/Field.svelte';
	import Input from '$lib/components/ui/Input.svelte';
	import Button from '$lib/components/ui/Button.svelte';
	import Switch from '$lib/components/ui/Switch.svelte';
	import Alert from '$lib/components/ui/Alert.svelte';
	import Spinner from '$lib/components/ui/Spinner.svelte';
	import Check from '@lucide/svelte/icons/check';

	const machine = getMachineContext();

	type NextcloudConfig = {
		enabled: boolean;
		server_url: string | null;
		remote_root: string;
		min_confidence: number | null;
		upload_unidentified: boolean;
		login_name: string | null;
		connected: boolean;
		needs_reconnect: boolean;
	};
	type NextcloudStatus = {
		running: boolean;
		pending: number;
		last_synced_seq: number;
		last_upload_at: number | null;
		last_error: string | null;
		needs_reconnect: boolean;
	};

	let config = $state<NextcloudConfig | null>(null);
	let status = $state<NextcloudStatus | null>(null);
	let loadError = $state<string | null>(null);

	let serverUrlDraft = $state('');
	let remoteRootDraft = $state('');
	let minConfidenceDraft = $state('');
	let uploadUnidentifiedDraft = $state(true);
	let enabledDraft = $state(false);

	let connecting = $state(false);
	let connectError = $state<string | null>(null);
	let savingConfig = $state(false);
	let saveError = $state<string | null>(null);
	let disconnecting = $state(false);

	let statusPollHandle: ReturnType<typeof setInterval> | null = null;
	let connectPollHandle: ReturnType<typeof setInterval> | null = null;

	function httpBase(): string {
		return machineHttpBaseUrlFromWsUrl(machine.machine?.url) ?? getBackendHttpBase();
	}

	function applyConfig(c: NextcloudConfig) {
		config = c;
		serverUrlDraft = c.server_url ?? '';
		remoteRootDraft = c.remote_root;
		minConfidenceDraft = c.min_confidence == null ? '' : String(c.min_confidence);
		uploadUnidentifiedDraft = c.upload_unidentified;
		enabledDraft = c.enabled;
	}

	async function loadConfig() {
		loadError = null;
		try {
			const res = await fetch(`${httpBase()}/api/nextcloud/config`);
			if (!res.ok) throw new Error(await res.text());
			applyConfig(await res.json());
		} catch (e: any) {
			loadError = e.message ?? 'Failed to load the Nextcloud settings';
		}
	}

	async function loadStatus() {
		try {
			const res = await fetch(`${httpBase()}/api/nextcloud/status`);
			if (!res.ok) return;
			status = await res.json();
		} catch {
			// Status is a background refresh; a failed poll just tries again.
		}
	}

	function isDraftDirty(): boolean {
		if (!config) return false;
		return (
			remoteRootDraft !== config.remote_root ||
			minConfidenceDraft !== (config.min_confidence == null ? '' : String(config.min_confidence)) ||
			uploadUnidentifiedDraft !== config.upload_unidentified ||
			enabledDraft !== config.enabled
		);
	}

	async function saveConfig() {
		savingConfig = true;
		saveError = null;
		try {
			const trimmedConfidence = minConfidenceDraft.trim();
			const res = await fetch(`${httpBase()}/api/nextcloud/config`, {
				method: 'POST',
				headers: { 'Content-Type': 'application/json' },
				body: JSON.stringify({
					remote_root: remoteRootDraft.trim() || '/SorterTrainingData',
					min_confidence: trimmedConfidence === '' ? null : Number(trimmedConfidence),
					upload_unidentified: uploadUnidentifiedDraft,
					enabled: enabledDraft
				})
			});
			if (!res.ok) throw new Error(await res.text());
			await loadConfig();
			await loadStatus();
		} catch (e: any) {
			saveError = e.message ?? 'Failed to save the Nextcloud settings';
		} finally {
			savingConfig = false;
		}
	}

	function stopConnectPoll() {
		if (connectPollHandle) {
			clearInterval(connectPollHandle);
			connectPollHandle = null;
		}
	}

	async function connect() {
		connecting = true;
		connectError = null;
		try {
			const url = serverUrlDraft.trim();
			if (!url) {
				connectError = 'Enter the Nextcloud server URL first.';
				connecting = false;
				return;
			}
			const res = await fetch(`${httpBase()}/api/nextcloud/connect/start`, {
				method: 'POST',
				headers: { 'Content-Type': 'application/json' },
				body: JSON.stringify({ server_url: url })
			});
			if (!res.ok) throw new Error(await res.text());
			const { login_url } = await res.json();
			window.open(login_url, '_blank', 'noopener');

			stopConnectPoll();
			connectPollHandle = setInterval(async () => {
				try {
					const sres = await fetch(`${httpBase()}/api/nextcloud/connect/status`);
					if (!sres.ok) return;
					const data = await sres.json();
					if (data.state === 'connected') {
						stopConnectPoll();
						connecting = false;
						await loadConfig();
						await loadStatus();
					} else if (data.state === 'failed' || data.state === 'timed_out') {
						stopConnectPoll();
						connecting = false;
						connectError =
							data.state === 'timed_out'
								? 'The login was not confirmed in time.'
								: 'The Nextcloud login failed.';
					}
				} catch {
					// keep polling
				}
			}, 2000);
		} catch (e: any) {
			connectError = e.message ?? 'Failed to connect to Nextcloud';
			connecting = false;
		}
	}

	async function disconnect() {
		disconnecting = true;
		try {
			const res = await fetch(`${httpBase()}/api/nextcloud/disconnect`, { method: 'POST' });
			if (!res.ok) throw new Error(await res.text());
			await loadConfig();
			await loadStatus();
		} catch (e: any) {
			connectError = e.message ?? 'Failed to disconnect';
		} finally {
			disconnecting = false;
		}
	}

	function formatTimestamp(ts: number | null): string {
		if (!ts) return 'never';
		return new Date(ts * 1000).toLocaleString();
	}

	onMount(async () => {
		await loadConfig();
		await loadStatus();
		statusPollHandle = setInterval(loadStatus, 15000);
	});

	onDestroy(() => {
		if (statusPollHandle) clearInterval(statusPollHandle);
		stopConnectPoll();
	});
</script>

<Panel
	title="Nextcloud"
	description="Uploads recognized pieces (image + part number + color) to your own Nextcloud, sorted so the data can train a custom model later."
	flush
>
	<div class="divide-y divide-line">
		{#if loadError}
			<div class="px-(--pad-panel) pt-(--pad-panel)"><Alert tone="danger">{loadError}</Alert></div>
		{/if}

		<div class="px-(--pad-panel) py-(--pad-panel)">
			{#if config?.connected}
				<div class="flex items-center gap-2 text-sm">
					<Check size={16} class="text-success-ink" />
					<span class="text-ink">
						Connected as <span class="font-medium">{config.login_name}</span> ({config.server_url})
					</span>
				</div>
				{#if config.needs_reconnect}
					<div class="mt-2"><Alert tone="warning">Nextcloud needs to be reconnected - it rejected the stored credentials.</Alert></div>
				{/if}
				<div class="mt-3">
					<Button variant="ghost" size="sm" loading={disconnecting} onclick={disconnect}>Disconnect</Button>
				</div>
			{:else}
				<Field label="Nextcloud server URL" for="nc-server-url" help="e.g. https://cloud.example.com">
					<Input id="nc-server-url" bind:value={serverUrlDraft} placeholder="https://cloud.example.com" />
				</Field>
				{#if connectError}
					<div class="mt-2"><Alert tone="danger">{connectError}</Alert></div>
				{/if}
				<div class="mt-3">
					<Button variant="primary" loading={connecting} onclick={connect}>Connect to Nextcloud</Button>
				</div>
				{#if connecting}
					<p class="mt-2 text-sm text-ink-muted">
						Log in to Nextcloud in the new tab and approve access - this page picks it up automatically.
					</p>
				{/if}
			{/if}
		</div>

		<SettingRow label="Uploading active" help="Send recognized pieces to Nextcloud as they come in.">
			<Switch bind:checked={enabledDraft} label="Uploading active" />
		</SettingRow>

		<SettingRow
			label="Target folder"
			help="Path in the Nextcloud where the data is stored."
			for="nc-remote-root"
		>
			<Input id="nc-remote-root" bind:value={remoteRootDraft} placeholder="/SorterTrainingData" class="w-56" />
		</SettingRow>

		<SettingRow
			label="Minimum confidence"
			help="Pieces below this score count as not confidently identified and go in the _unidentified folder. Leave blank for no threshold."
			for="nc-min-confidence"
		>
			<Input
				id="nc-min-confidence"
				type="number"
				min="0"
				max="1"
				step="0.05"
				bind:value={minConfidenceDraft}
				placeholder="e.g. 0.8"
				class="w-28"
			/>
		</SettingRow>

		<SettingRow
			label="Upload unidentified pieces"
			help="Also upload pieces with no confident match, into their own _unidentified folder - often the most valuable future training data."
		>
			<Switch bind:checked={uploadUnidentifiedDraft} label="Upload unidentified pieces" />
		</SettingRow>

		{#if saveError}
			<div class="px-(--pad-panel) py-2"><Alert tone="danger">{saveError}</Alert></div>
		{/if}
		<div class="flex justify-end px-(--pad-panel) py-(--pad-row)">
			<Button variant="primary" loading={savingConfig} disabled={!isDraftDirty()} onclick={saveConfig}>
				Save
			</Button>
		</div>

		{#if status}
			<div class="px-(--pad-panel) py-(--pad-panel) text-sm text-ink-muted">
				<div>Pending: <span class="font-mono text-ink">{status.pending}</span></div>
				<div>Last upload: {formatTimestamp(status.last_upload_at)}</div>
				{#if status.last_error}
					<div class="mt-1"><Alert tone="danger">{status.last_error}</Alert></div>
				{/if}
			</div>
		{:else}
			<div class="flex items-center gap-2 px-(--pad-panel) py-(--pad-panel) text-sm text-ink-muted">
				<Spinner size={14} /> Loading status
			</div>
		{/if}
	</div>
</Panel>
