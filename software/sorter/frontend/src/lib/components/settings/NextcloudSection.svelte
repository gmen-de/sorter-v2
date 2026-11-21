<script lang="ts">
	import { onMount, onDestroy } from 'svelte';
	import { _ } from 'svelte-i18n';
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
			loadError = e.message ?? $_('components.settings.nextcloudSection.errors.loadFailed');
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
			saveError = e.message ?? $_('components.settings.nextcloudSection.errors.saveFailed');
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
				connectError = $_('components.settings.nextcloudSection.errors.enterServerUrl');
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
								? $_('components.settings.nextcloudSection.errors.loginTimedOut')
								: $_('components.settings.nextcloudSection.errors.loginFailed');
					}
				} catch {
					// keep polling
				}
			}, 2000);
		} catch (e: any) {
			connectError = e.message ?? $_('components.settings.nextcloudSection.errors.connectFailed');
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
			connectError = e.message ?? $_('components.settings.nextcloudSection.errors.disconnectFailed');
		} finally {
			disconnecting = false;
		}
	}

	function formatTimestamp(ts: number | null): string {
		if (!ts) return $_('components.settings.nextcloudSection.neverLabel');
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
	title={$_('components.settings.nextcloudSection.panelTitle')}
	description={$_('components.settings.nextcloudSection.panelDescription')}
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
						{$_('components.settings.nextcloudSection.connectedAs')}
						<span class="font-medium">{config.login_name}</span> ({config.server_url})
					</span>
				</div>
				{#if config.needs_reconnect}
					<div class="mt-2">
						<Alert tone="warning">{$_('components.settings.nextcloudSection.needsReconnect')}</Alert>
					</div>
				{/if}
				<div class="mt-3">
					<Button variant="ghost" size="sm" loading={disconnecting} onclick={disconnect}
						>{$_('components.settings.nextcloudSection.disconnectButton')}</Button
					>
				</div>
			{:else}
				<Field
					label={$_('components.settings.nextcloudSection.serverUrl.label')}
					for="nc-server-url"
					help={$_('components.settings.nextcloudSection.serverUrl.help')}
				>
					<Input id="nc-server-url" bind:value={serverUrlDraft} placeholder="https://cloud.example.com" />
				</Field>
				{#if connectError}
					<div class="mt-2"><Alert tone="danger">{connectError}</Alert></div>
				{/if}
				<div class="mt-3">
					<Button variant="primary" loading={connecting} onclick={connect}
						>{$_('components.settings.nextcloudSection.connectButton')}</Button
					>
				</div>
				{#if connecting}
					<p class="mt-2 text-sm text-ink-muted">
						{$_('components.settings.nextcloudSection.connectingHint')}
					</p>
				{/if}
			{/if}
		</div>

		<SettingRow
			label={$_('components.settings.nextcloudSection.uploadingActive.label')}
			help={$_('components.settings.nextcloudSection.uploadingActive.help')}
		>
			<Switch
				bind:checked={enabledDraft}
				label={$_('components.settings.nextcloudSection.uploadingActive.label')}
			/>
		</SettingRow>

		<SettingRow
			label={$_('components.settings.nextcloudSection.targetFolder.label')}
			help={$_('components.settings.nextcloudSection.targetFolder.help')}
			for="nc-remote-root"
		>
			<Input id="nc-remote-root" bind:value={remoteRootDraft} placeholder="/SorterTrainingData" class="w-56" />
		</SettingRow>

		<SettingRow
			label={$_('components.settings.nextcloudSection.minConfidence.label')}
			help={$_('components.settings.nextcloudSection.minConfidence.help')}
			for="nc-min-confidence"
		>
			<Input
				id="nc-min-confidence"
				type="number"
				min="0"
				max="1"
				step="0.05"
				bind:value={minConfidenceDraft}
				placeholder={$_('components.settings.nextcloudSection.minConfidence.placeholder')}
				class="w-28"
			/>
		</SettingRow>

		<SettingRow
			label={$_('components.settings.nextcloudSection.uploadUnidentified.label')}
			help={$_('components.settings.nextcloudSection.uploadUnidentified.help')}
		>
			<Switch
				bind:checked={uploadUnidentifiedDraft}
				label={$_('components.settings.nextcloudSection.uploadUnidentified.label')}
			/>
		</SettingRow>

		{#if saveError}
			<div class="px-(--pad-panel) py-2"><Alert tone="danger">{saveError}</Alert></div>
		{/if}
		<div class="flex justify-end px-(--pad-panel) py-(--pad-row)">
			<Button variant="primary" loading={savingConfig} disabled={!isDraftDirty()} onclick={saveConfig}>
				{$_('components.settings.nextcloudSection.saveButton')}
			</Button>
		</div>

		{#if status}
			<div class="px-(--pad-panel) py-(--pad-panel) text-sm text-ink-muted">
				<div>
					{$_('components.settings.nextcloudSection.status.pending')}
					<span class="font-mono text-ink">{status.pending}</span>
				</div>
				<div>
					{$_('components.settings.nextcloudSection.status.lastUpload')} {formatTimestamp(
						status.last_upload_at
					)}
				</div>
				{#if status.last_error}
					<div class="mt-1"><Alert tone="danger">{status.last_error}</Alert></div>
				{/if}
			</div>
		{:else}
			<div class="flex items-center gap-2 px-(--pad-panel) py-(--pad-panel) text-sm text-ink-muted">
				<Spinner size={14} /> {$_('components.settings.nextcloudSection.status.loading')}
			</div>
		{/if}
	</div>
</Panel>
