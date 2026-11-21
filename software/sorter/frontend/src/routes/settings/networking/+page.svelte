<script lang="ts">
	import { _ } from 'svelte-i18n';
	import PageHeader from '$lib/components/ui/PageHeader.svelte';
	import Panel from '$lib/components/ui/Panel.svelte';
	import ConnectionSection from '$lib/components/settings/ConnectionSection.svelte';
	import TailscaleSection from '$lib/components/settings/TailscaleSection.svelte';
	import WifiSection from '$lib/components/settings/WifiSection.svelte';
</script>

<svelte:head><title>{$_('routes.settings.networking.pageTitle')}</title></svelte:head>

<PageHeader title={$_('routes.settings.networking.title')} description={$_('routes.settings.networking.description')} />

<ConnectionSection />

<Panel title={$_('routes.settings.networking.wifi.title')} description={$_('routes.settings.networking.wifi.description')}>
	<WifiSection />
</Panel>

<Panel title={$_('routes.settings.networking.tailscale.title')} description={$_('routes.settings.networking.tailscale.description')}>
	<TailscaleSection />
</Panel>
