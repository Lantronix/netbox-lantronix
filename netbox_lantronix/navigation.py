from netbox.plugins import PluginMenu, PluginMenuButton, PluginMenuItem


def _add(link, perm):
    return (
        PluginMenuButton(
            link=link, title="Add", icon_class="mdi mdi-plus-thick", permissions=[perm]
        ),
    )


menu = PluginMenu(
    label="Lantronix",
    icon_class="mdi mdi-console-network",
    groups=(
        (
            "Percepxion",
            (
                PluginMenuItem(
                    link="plugins:netbox_lantronix:percepxionsource_list",
                    link_text="Sources",
                    permissions=["netbox_lantronix.view_percepxionsource"],
                    buttons=_add(
                        "plugins:netbox_lantronix:percepxionsource_add",
                        "netbox_lantronix.add_percepxionsource",
                    ),
                ),
                PluginMenuItem(
                    link="plugins:netbox_lantronix:syncfinding_list",
                    link_text="Findings",
                    permissions=["netbox_lantronix.view_syncfinding"],
                ),
                PluginMenuItem(
                    link="plugins:netbox_lantronix:devicelink_list",
                    link_text="Device links",
                    permissions=["netbox_lantronix.view_devicelink"],
                ),
            ),
        ),
        (
            "Device types",
            (
                PluginMenuItem(
                    link="plugins:netbox_lantronix:modelrule_list",
                    link_text="Model rules",
                    permissions=["netbox_lantronix.view_modelrule"],
                    buttons=_add(
                        "plugins:netbox_lantronix:modelrule_add", "netbox_lantronix.add_modelrule"
                    ),
                ),
                PluginMenuItem(
                    link="plugins:netbox_lantronix:devicetypeoverride_list",
                    link_text="Device overrides",
                    permissions=["netbox_lantronix.view_devicetypeoverride"],
                    buttons=_add(
                        "plugins:netbox_lantronix:devicetypeoverride_add",
                        "netbox_lantronix.add_devicetypeoverride",
                    ),
                ),
            ),
        ),
    ),
)
