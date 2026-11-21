<script lang="ts">
	import { onMount } from 'svelte';
	import { _ } from 'svelte-i18n';
	import { getBackendHttpBase, machineHttpBaseUrlFromWsUrl } from '$lib/backend';
	import { getMachineContext } from '$lib/machines/context';
	import Field from '$lib/components/ui/Field.svelte';
	import Input from '$lib/components/ui/Input.svelte';
	import Button from '$lib/components/ui/Button.svelte';
	import Badge from '$lib/components/ui/Badge.svelte';
	import Alert from '$lib/components/ui/Alert.svelte';

	const machine = getMachineContext();

	type Provider = {
		id: string;
		label: string;
		envVar: string;
		placeholder: string;
	};

	const PROVIDER: Provider = {
		id: 'openrouter',
		label: 'OpenRouter',
		envVar: 'OPENROUTER_API_KEY',
		placeholder: 'sk-or-v1-...'
	};

	let savedKeys = $state<Record<string, string | null>>({});
	let inputKeys = $state<Record<string, string>>({});
	let saving = $state<Record<string, boolean>>({});
	let statusMsg = $state<string | null>(null);
	let errorMsg = $state<string | null>(null);
	let loading = $state(true);

	function currentBackendBaseUrl(): string {
		return machineHttpBaseUrlFromWsUrl(machine.machine?.url) ?? getBackendHttpBase();
	}

	async function loadKeys() {
		loading = true;
		try {
			const res = await fetch(`${currentBackendBaseUrl()}/api/settings/api-keys`);
			if (!res.ok) throw new Error(await res.text());
			const data = await res.json();
			savedKeys = data.keys ?? {};
		} catch (e: any) {
			errorMsg = e.message ?? $_('components.settings.apiKeysSection.loadError');
		} finally {
			loading = false;
		}
	}

	async function saveKey(providerId: string) {
		const key = inputKeys[providerId]?.trim();
		if (!key) return;
		saving = { ...saving, [providerId]: true };
		errorMsg = null;
		statusMsg = null;
		try {
			const res = await fetch(`${currentBackendBaseUrl()}/api/settings/api-keys`, {
				method: 'POST',
				headers: { 'Content-Type': 'application/json' },
				body: JSON.stringify({ provider: providerId, key })
			});
			if (!res.ok) throw new Error(await res.text());
			const data = await res.json();
			statusMsg = data.message ?? $_('components.settings.apiKeysSection.saved');
			inputKeys[providerId] = '';
			await loadKeys();
		} catch (e: any) {
			errorMsg = e.message ?? $_('components.settings.apiKeysSection.saveError');
		} finally {
			saving = { ...saving, [providerId]: false };
		}
	}

	onMount(() => {
		void loadKeys();
	});
</script>

<div class="flex flex-col gap-4">
	<div class="flex items-center gap-2">
		<span class="text-sm font-medium text-ink">
			{$_('components.settings.apiKeysSection.providerKeyLabel', { values: { provider: PROVIDER.label } })}
		</span>
		{#if savedKeys[PROVIDER.id]}
			<Badge tone="success" dot><span class="font-mono">{savedKeys[PROVIDER.id]}</span></Badge>
		{:else}
			<Badge tone="warning" dot>{$_('components.settings.apiKeysSection.notSet')}</Badge>
		{/if}
	</div>
	<Field
		label={$_('components.settings.apiKeysSection.newKey.label')}
		for="openrouter-key"
		help={$_('components.settings.apiKeysSection.newKey.help', { values: { envVar: PROVIDER.envVar } })}
	>
		<div class="flex gap-2">
			<Input
				id="openrouter-key"
				type="password"
				placeholder={PROVIDER.placeholder}
				bind:value={inputKeys[PROVIDER.id]}
				class="min-w-0 flex-1 font-mono"
			/>
			<Button
				loading={saving[PROVIDER.id]}
				disabled={!inputKeys[PROVIDER.id]?.trim()}
				onclick={() => void saveKey(PROVIDER.id)}
			>
				{$_('components.settings.apiKeysSection.save')}
			</Button>
		</div>
	</Field>
	{#if errorMsg}
		<Alert tone="danger">{errorMsg}</Alert>
	{/if}
	{#if statusMsg}
		<p class="text-sm text-ink-muted">{statusMsg}</p>
	{/if}
</div>
