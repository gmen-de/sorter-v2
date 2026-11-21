<script lang="ts">
	import { _ } from 'svelte-i18n';
	import { getBackendHttpBase } from '$lib/backend';
	import Alert from '$lib/components/ui/Alert.svelte';
	import Panel from '$lib/components/ui/Panel.svelte';
	import PageHeader from '$lib/components/ui/PageHeader.svelte';
	import SettingsSaveBar from '$lib/components/settings/SettingsSaveBar.svelte';
	import Spinner from '$lib/components/ui/Spinner.svelte';
	import ProviderSelect from '$lib/components/settings/ProviderSelect.svelte';
	import type { ProviderInfo } from '$lib/components/settings/ProviderSelect.svelte';

	let colorProviders = $state<ProviderInfo[]>([]);
	let moldProviders = $state<ProviderInfo[]>([]);
	let activeColor = $state('');
	let activeMold = $state('');
	let selectedColor = $state('');
	let selectedMold = $state('');
	let loading = $state(true);
	let saving = $state(false);
	let error = $state<string | null>(null);
	let saved = $state(false);

	let currentColor = $derived(colorProviders.find((p) => p.id === selectedColor));
	let currentMold = $derived(moldProviders.find((p) => p.id === selectedMold));

	function applyData(data: any) {
		colorProviders = data.color_providers ?? [];
		moldProviders = data.mold_providers ?? [];
		activeColor = data.active?.color_provider ?? '';
		activeMold = data.active?.mold_provider ?? '';
		selectedColor = activeColor;
		selectedMold = activeMold;
	}

	async function load() {
		loading = true;
		error = null;
		try {
			const res = await fetch(`${getBackendHttpBase()}/api/tuning/classification-providers`);
			if (!res.ok) throw new Error(`HTTP ${res.status}`);
			applyData(await res.json());
		} catch (e: any) {
			error = e.message ?? $_('routes.settings.providers.errors.load');
		} finally {
			loading = false;
		}
	}

	async function save() {
		saving = true;
		saved = false;
		error = null;
		try {
			const res = await fetch(`${getBackendHttpBase()}/api/tuning/classification-providers`, {
				method: 'POST',
				headers: { 'Content-Type': 'application/json' },
				body: JSON.stringify({
					color_provider: selectedColor,
					mold_provider: selectedMold
				})
			});
			if (!res.ok) {
				const body = await res.json().catch(() => ({}));
				throw new Error(body.detail ?? `HTTP ${res.status}`);
			}
			applyData(await res.json());
			saved = true;
			setTimeout(() => (saved = false), 3000);
		} catch (e: any) {
			error = e.message ?? $_('routes.settings.providers.errors.save');
		} finally {
			saving = false;
		}
	}

	$effect(() => {
		load();
	});
</script>

<svelte:head><title>{$_('routes.settings.providers.pageTitle')}</title></svelte:head>

<PageHeader
	title={$_('routes.settings.providers.title')}
	description={$_('routes.settings.providers.description')}
/>

{#if error}
	<Alert tone="danger">{error}</Alert>
{/if}
{#if saved}
	<Alert tone="success">{$_('routes.settings.providers.saved')}</Alert>
{/if}

{#if loading}
	<div class="flex items-center gap-2 text-sm text-ink-muted"><Spinner size={14} /> {$_('routes.settings.providers.loading')}</div>
{:else}
	<Panel title={$_('routes.settings.providers.color.panelTitle')} description={$_('routes.settings.providers.color.panelDescription')}>
		<ProviderSelect
			name={$_('routes.settings.providers.color.fieldName')}
			options={colorProviders}
			bind:selected={selectedColor}
			active={activeColor}
		/>
	</Panel>

	<Panel title={$_('routes.settings.providers.mold.panelTitle')} description={$_('routes.settings.providers.mold.panelDescription')}>
		<ProviderSelect
			name={$_('routes.settings.providers.mold.fieldName')}
			options={moldProviders}
			bind:selected={selectedMold}
			active={activeMold}
		/>
	</Panel>

	<SettingsSaveBar {save} reset={load} {saving} />
{/if}
