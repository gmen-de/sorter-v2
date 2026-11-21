<script lang="ts">
	import { _ } from 'svelte-i18n';
	import NextcloudSection from '$lib/components/settings/NextcloudSection.svelte';
	import PageHeader from '$lib/components/ui/PageHeader.svelte';
</script>

<svelte:head><title>{$_('routes.settings.nextcloud.pageTitle')}</title></svelte:head>

<PageHeader
	title={$_('routes.settings.nextcloud.title')}
	description={$_('routes.settings.nextcloud.description')}
/>
<NextcloudSection />
