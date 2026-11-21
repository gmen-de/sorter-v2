<script lang="ts">
	import { _ } from 'svelte-i18n';
	import IncidentHandlingSection from '$lib/components/settings/IncidentHandlingSection.svelte';
	import Panel from '$lib/components/ui/Panel.svelte';
	import PageHeader from '$lib/components/ui/PageHeader.svelte';
</script>

<svelte:head><title>{$_('routes.settings.incidents.pageTitle')}</title></svelte:head>

<PageHeader
	title={$_('routes.settings.incidents.title')}
	description={$_('routes.settings.incidents.description')}
/>

<Panel flush>
	<IncidentHandlingSection />
</Panel>
