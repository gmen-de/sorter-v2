<script lang="ts">
	import { _ } from 'svelte-i18n';
	import HiveModelsSection from '$lib/components/settings/HiveModelsSection.svelte';
	import HiveRuntimesSection from '$lib/components/settings/HiveRuntimesSection.svelte';
	import Panel from '$lib/components/ui/Panel.svelte';
	import PageHeader from '$lib/components/ui/PageHeader.svelte';
</script>

<svelte:head><title>{$_('routes.settings.hive.models.pageTitle')}</title></svelte:head>

<PageHeader
	title={$_('routes.settings.hive.models.title')}
	description={$_('routes.settings.hive.models.description')}
/>

<HiveModelsSection />

<Panel
	title={$_('routes.settings.hive.models.compatibility.title')}
	description={$_('routes.settings.hive.models.compatibility.description')}
>
	<HiveRuntimesSection />
</Panel>
