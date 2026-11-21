<script lang="ts">
	import { _ } from 'svelte-i18n';
	import { getBackendHttpBase } from '$lib/backend';
	import Alert from '$lib/components/ui/Alert.svelte';
	import Panel from '$lib/components/ui/Panel.svelte';
	import TuningParamRow from '$lib/components/settings/TuningParamRow.svelte';
	import type { TuningFieldMeta, TuningValues } from '$lib/settings/tuning';
	import PageHeader from '$lib/components/ui/PageHeader.svelte';
	import SettingsSaveBar from '$lib/components/settings/SettingsSaveBar.svelte';
	import Spinner from '$lib/components/ui/Spinner.svelte';

	let fields = $state<TuningFieldMeta[]>([]);
	let values = $state<TuningValues>({});
	let loading = $state(true);
	let saving = $state(false);
	let error = $state<string | null>(null);
	let saved = $state(false);

	async function load() {
		loading = true;
		error = null;
		try {
			const res = await fetch(`${getBackendHttpBase()}/api/tuning/classification-channel-rev01`);
			if (!res.ok) throw new Error(`HTTP ${res.status}`);
			const data = await res.json();
			fields = data.fields;
			values = { ...data.config };
		} catch (e: any) {
			error = e.message ?? $_('routes.settings.tuning.classificationChannel.errors.load');
		} finally {
			loading = false;
		}
	}

	async function save() {
		saving = true;
		saved = false;
		error = null;
		try {
			const res = await fetch(`${getBackendHttpBase()}/api/tuning/classification-channel-rev01`, {
				method: 'POST',
				headers: { 'Content-Type': 'application/json' },
				body: JSON.stringify(values)
			});
			if (!res.ok) {
				const body = await res.json().catch(() => ({}));
				throw new Error(body.detail ?? `HTTP ${res.status}`);
			}
			const data = await res.json();
			values = { ...data.config };
			saved = true;
			setTimeout(() => (saved = false), 3000);
		} catch (e: any) {
			error = e.message ?? $_('routes.settings.tuning.classificationChannel.errors.save');
		} finally {
			saving = false;
		}
	}

	$effect(() => {
		load();
	});
</script>

<svelte:head><title>{$_('routes.settings.tuning.classificationChannel.pageTitle')}</title></svelte:head>

<PageHeader
	title={$_('routes.settings.tuning.classificationChannel.title')}
	description={$_('routes.settings.tuning.classificationChannel.description')}
/>

{#if error}
	<Alert tone="danger">{error}</Alert>
{/if}
{#if saved}
	<Alert tone="success">{$_('routes.settings.tuning.classificationChannel.saved')}</Alert>
{/if}

<Panel title={$_('routes.settings.tuning.classificationChannel.parametersPanelTitle')} flush>
	{#if loading}
		<div class="px-(--pad-panel) pb-(--pad-panel)"><div class="flex items-center gap-2 text-sm text-ink-muted"><Spinner size={14} /> {$_('routes.settings.tuning.classificationChannel.loading')}</div></div>
	{:else}
		<div class="divide-y divide-line">
			{#each fields as field}
				<TuningParamRow {field} bind:values />
			{/each}
		</div>
	{/if}
	{#snippet footer()}
		<SettingsSaveBar {save} reset={load} {saving} disabled={loading} />
	{/snippet}
</Panel>
