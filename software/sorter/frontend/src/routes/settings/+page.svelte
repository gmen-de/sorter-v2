<script lang="ts">
	import { _ } from 'svelte-i18n';
	import PageHeader from '$lib/components/ui/PageHeader.svelte';
	import Panel from '$lib/components/ui/Panel.svelte';
	import GeneralSection from '$lib/components/settings/GeneralSection.svelte';
	import ApiKeysSection from '$lib/components/settings/ApiKeysSection.svelte';
</script>

<svelte:head><title>{$_('routes.settings.index.pageTitle')}</title></svelte:head>

<PageHeader
	title={$_('routes.settings.index.title')}
	description={$_('routes.settings.index.description')}
/>

<GeneralSection />

<Panel
	title={$_('routes.settings.index.openRouter.title')}
	description={$_('routes.settings.index.openRouter.description')}
>
	<ApiKeysSection />
</Panel>
