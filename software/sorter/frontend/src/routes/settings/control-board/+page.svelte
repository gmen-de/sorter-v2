<script lang="ts">
	import { _ } from 'svelte-i18n';
	import ControlBoardSection from '$lib/components/settings/ControlBoardSection.svelte';
	import PageHeader from '$lib/components/ui/PageHeader.svelte';
</script>

<svelte:head><title>{$_('routes.settings.controlBoard.pageTitle')}</title></svelte:head>

<PageHeader
	title={$_('routes.settings.controlBoard.title')}
	description={$_('routes.settings.controlBoard.description')}
/>
<ControlBoardSection />
