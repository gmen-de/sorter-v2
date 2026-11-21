<script lang="ts">
	import { _ } from 'svelte-i18n';
	import PageHeader from '$lib/components/ui/PageHeader.svelte';
	import AppearanceSection from '$lib/components/settings/AppearanceSection.svelte';
</script>

<svelte:head><title>{$_('routes.settings.appearance.pageTitle')}</title></svelte:head>

<PageHeader title={$_('routes.settings.appearance.title')} description={$_('routes.settings.appearance.description')} />

<AppearanceSection />
