import { describe, it, expect, vi } from 'vitest'
import { screen } from '@testing-library/react'
import { renderWithRouter } from '../../test/test-utils'
import Enforcement from '../Enforcement'

vi.mock('../../services/api')

describe('<Enforcement />', () => {
  it('renders the enforcement actions header', async () => {
    renderWithRouter(<Enforcement />)
    expect(await screen.findByRole('heading', { name: /Enforcement actions/i })).toBeInTheDocument()
  })
})
