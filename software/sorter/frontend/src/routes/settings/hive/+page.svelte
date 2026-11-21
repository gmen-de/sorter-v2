<script lang="ts">
	import { _ } from 'svelte-i18n';
	import HiveSection from '$lib/components/settings/HiveSection.svelte';
	import PageHeader from '$lib/components/ui/PageHeader.svelte';
</script>

<svelte:head><title>{$_('routes.settings.hive.page.pageTitle')}</title></svelte:head>

<PageHeader
	title={$_('routes.settings.hive.page.title')}
	description={$_('routes.settings.hive.page.description')}
/>
<HiveSection />
