<b>Material Request Type</b>: {{ doc.material_request_type }}<br>
<b>Company</b>: {{ doc.company }}

<h3>Order Summary</h3>

<table border=2 >
    <tr align="center">
        <th>Product Name</th>
        <th>Received Quantity</th>
    </tr>
    {% for product in doc.products %}
        {% if frappe.utils.flt(product.received_qty, 2) > 0.0 %}
            <tr align="center">
                <td>{{ product.product_code }}</td>
                <td>{{ frappe.utils.flt(product.received_qty, 2) }}</td>
            </tr>
        {% endif %}
    {% endfor %}
</table>